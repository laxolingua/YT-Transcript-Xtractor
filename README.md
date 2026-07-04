# Transcript Copier

A deliberately tiny mobile-first web app:

1. Paste a YouTube URL.
2. Extract an available caption track.
3. Copy, share, or download the cleaned transcript.

No accounts, ads, analytics, database, or transcript storage.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## Docker

```bash
docker build -t transcript-copier .
docker run --rm -p 8000:8000 transcript-copier
```

## Install on iPhone

Deploy the app over HTTPS, open it in Safari, tap **Share**, then **Add to Home Screen**.

## Limits

- The video must publicly expose captions or an automatic transcript.
- Private, age-restricted, geo-blocked, members-only, or bot-protected videos may fail.
- YouTube changes its delivery mechanisms periodically, so `yt-dlp` should be updated regularly.
- This app does not download video or generate a new transcript from audio.
