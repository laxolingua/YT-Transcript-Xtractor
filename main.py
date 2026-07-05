from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl
from youtube_transcript_api import YouTubeTranscriptApi

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR

app = FastAPI(title="Transcript Copier", version="2.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ExtractRequest(BaseModel):
    url: HttpUrl
    language: str | None = None
    include_timestamps: bool = False


def _video_id(url: str) -> str | None:
    m = re.search(r"(?:v=|/shorts/|youtu\.be/|/embed/|/live/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def _fmt(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _paragraphs(snippets) -> str:
    out: list[str] = []
    para: list[str] = []
    n = 0
    for sn in snippets:
        t = sn.text.strip()
        if not t:
            continue
        para.append(t)
        n += len(t) + 1
        if n >= 700 or (t.endswith((".", "!", "?")) and n >= 350):
            out.append(" ".join(para))
            para, n = [], 0
    if para:
        out.append(" ".join(para))
    return "\n\n".join(out)


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
    vid = _video_id(url)
    if not vid:
        raise HTTPException(status_code=400, detail="Please enter a valid YouTube URL.")

    ytt = YouTubeTranscriptApi()
    try:
        tlist = ytt.list(vid)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"YouTube could not be reached: {exc}") from exc

    try:
        available = [t.language_code for t in tlist]
    except Exception:
        available = []

    prefs: list[str] = []
    if payload.language:
        prefs.append(payload.language)
    prefs += ["en", "en-US", "en-GB", "es", "de"]

    tr = None
    try:
        tr = tlist.find_transcript(prefs)
    except Exception:
        try:
            tr = next(iter(tlist))
        except Exception:
            raise HTTPException(status_code=404, detail="This video has no transcript or captions.")

    try:
        fetched = tr.fetch()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"The transcript could not be downloaded: {exc}") from exc

    if payload.include_timestamps:
        transcript = "\n".join(
            f"[{_fmt(sn.start)}] {sn.text.strip()}" for sn in fetched if sn.text.strip()
        )
    else:
        transcript = _paragraphs(fetched)

    if not transcript.strip():
        raise HTTPException(status_code=404, detail="The transcript was empty.")

    return {
        "title": "YouTube video",
        "channel": "",
        "language": getattr(tr, "language_code", payload.language or ""),
        "available_languages": sorted(available),
        "transcript": transcript,
        "video_url": url,
    }
