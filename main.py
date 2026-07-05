from __future__ import annotations

import html
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl
from yt_dlp import YoutubeDL

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR

app = FastAPI(title="Transcript Copier", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ExtractRequest(BaseModel):
    url: HttpUrl
    language: str | None = None
    include_timestamps: bool = False


def _is_youtube_url(url: str) -> bool:
    return bool(re.search(r"(?:youtube\.com|youtu\.be)", url, re.I))


def _clean_caption_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _timestamp_to_seconds(ts: str) -> float:
    ts = ts.replace(",", ".")
    parts = ts.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
    except ValueError:
        return 0.0
    return 0.0


def _format_timestamp(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _parse_vtt(content: str, include_timestamps: bool) -> str:
    lines = content.splitlines()
    segments: list[tuple[float, str]] = []
    current_start = 0.0
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        text = _clean_caption_text(" ".join(buffer))
        if text:
            segments.append((current_start, text))
        buffer = []

    for raw in lines:
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if re.fullmatch(r"\d+", line):
            continue
        if "-->" in line:
            flush()
            current_start = _timestamp_to_seconds(line.split("-->", 1)[0].strip())
            continue
        if line.startswith(("Kind:", "Language:")):
            continue
        buffer.append(line)
    flush()

    deduped: list[tuple[float, str]] = []
    last_text = ""
    for start, text in segments:
        if not text or text == last_text:
            continue
        # YouTube rolling captions sometimes repeat the previous line as a prefix.
        if last_text and text.startswith(last_text):
            text = text[len(last_text):].strip()
        elif last_text and last_text.startswith(text):
            continue
        if text:
            deduped.append((start, text))
            last_text = text

    if include_timestamps:
        return "\n".join(f"[{_format_timestamp(start)}] {text}" for start, text in deduped)

    paragraphs: list[str] = []
    paragraph: list[str] = []
    char_count = 0
    for _, text in deduped:
        paragraph.append(text)
        char_count += len(text) + 1
        if char_count >= 700 or text.endswith((".", "!", "?")) and char_count >= 350:
            paragraphs.append(" ".join(paragraph))
            paragraph = []
            char_count = 0
    if paragraph:
        paragraphs.append(" ".join(paragraph))
    return "\n\n".join(paragraphs)


def _subtitle_candidates(info: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for source_key in ("subtitles", "automatic_captions"):
        source = info.get(source_key) or {}
        for lang, tracks in source.items():
            result.setdefault(lang, []).extend(tracks or [])
    return result


def _pick_track(candidates: dict[str, list[dict[str, Any]]], preferred: str | None) -> tuple[str, dict[str, Any]]:
    if not candidates:
        raise HTTPException(status_code=404, detail="This video does not expose a transcript or captions.")

    languages = list(candidates.keys())
    chosen_lang = None
    if preferred:
        preferred_lower = preferred.lower()
        chosen_lang = next((lang for lang in languages if lang.lower() == preferred_lower), None)
        if not chosen_lang:
            chosen_lang = next((lang for lang in languages if lang.lower().startswith(preferred_lower + "-")), None)

    if not chosen_lang:
        priority = ["en", "en-US", "en-GB", "es", "de"]
        chosen_lang = next((lang for lang in priority if lang in candidates), languages[0])

    tracks = candidates[chosen_lang]
    preferred_exts = ("vtt", "srv3", "srv2", "srv1", "ttml", "json3")
    for ext in preferred_exts:
        track = next((t for t in tracks if t.get("ext") == ext and t.get("url")), None)
        if track:
            return chosen_lang, track
    track = next((t for t in tracks if t.get("url")), None)
    if not track:
        raise HTTPException(status_code=404, detail="A transcript was listed, but no downloadable caption track was available.")
    return chosen_lang, track


def _download_text(url: str) -> str:
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.webmanifest")
def manifest() -> FileResponse:
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/service-worker.js")
def service_worker() -> FileResponse:
    return FileResponse(STATIC_DIR / "service-worker.js", media_type="application/javascript")


@app.post("/api/extract")
def extract_transcript(payload: ExtractRequest) -> dict[str, Any]:
    url = str(payload.url)
    if not _is_youtube_url(url):
        raise HTTPException(status_code=400, detail="Please enter a valid YouTube URL.")

    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "no_warnings": True,
        "extract_flat": False,
        "socket_timeout": 30,
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"YouTube could not be reached: {exc}") from exc

    if not isinstance(info, dict):
        raise HTTPException(status_code=502, detail="Could not read this video.")

    candidates = _subtitle_candidates(info)
    language, track = _pick_track(candidates, payload.language)

    try:
        content = _download_text(track["url"])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"The caption track could not be downloaded: {exc}") from exc

    transcript = _parse_vtt(content, payload.include_timestamps)
    if not transcript.strip():
        raise HTTPException(status_code=404, detail="The caption track was empty or unreadable.")

    return {
        "title": info.get("title") or "YouTube video",
        "channel": info.get("channel") or info.get("uploader") or "",
        "language": language,
        "available_languages": sorted(candidates.keys()),
        "transcript": transcript,
        "video_url": info.get("webpage_url") or url,
    }
