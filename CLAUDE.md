# GTA Clips – Hinweise für Claude

Server-Version des Clipping-Systems (vorher lokal auf dem Laptop, nach dem Herr-Tech-Prompt „Weg 03“).
Python/FastAPI-Backend + PWA ohne Build-Schritt. Antworten an den Nutzer auf Deutsch.

- `app/watcher.py` – Live-Status (Twitch Helix, YouTube via yt-dlp), Chat-Tasks, Hype → Clip-Auftrag, Clip-Worker
- `app/hype.py` – Hype-Erkennung (Nachrichten/s gegen gleitenden Normalwert + Clip-Wörter), reine Logik, getestet
- `app/chat.py` – Twitch-Chat anonym über IRC-WebSocket (kein Token nötig), YouTube über chat-downloader
- `app/clipper.py` – yt-dlp-Abschnitt laden, ffmpeg 9:16 (Layout `blur`/`crop`), ASS-Overlay
- `app/captions.py` – optionale Untertitel (faster-whisper, nur mit `ENABLE_CAPTIONS=1`), ASS mit Hook
- `app/hook.py` – Hook + Caption per Claude (`ANTHROPIC_API_KEY`), sonst Vorlage
- `app/rules.py` – Regel-Check vor dem Posten, Einnahmen-Schätzung, Import einer `kampagnen.json`
- `app/main.py` – API (Passwort-Login per signiertem Cookie), liefert `app/static/` (PWA) aus
- Daten: SQLite + Clips unter `DATA_DIR` (im Container `/data`)

Regeln: Schlüssel nur in `.env` auf dem Server, nie committen. Nichts postet automatisch; Posten nur nach
bestandenem Regel-Check eintragen. Tests: `python -m pytest -q` (braucht ffmpeg).
