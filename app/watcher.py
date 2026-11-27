"""Beobachtet die Streamer-Liste: Live-Status, Chat, Hype -> Clip-Auftrag -> fertiger Clip."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any

from . import captions, clipper
from .config import Settings
from .db import DB
from .hook import make_hook
from .hype import Hype, HypeDetector, find_keywords
from .chat import twitch_chat, youtube_chat
from .twitch import Twitch, parse_time

log = logging.getLogger("watcher")


def youtube_live_url(channel: str) -> str:
    c = channel.strip()
    if c.startswith("http"):
        return c.rstrip("/") + ("" if c.rstrip("/").endswith("/live") or "watch?v=" in c else "/live")
    if c.startswith("UC"):
        return f"https://www.youtube.com/channel/{c}/live"
    return f"https://www.youtube.com/{c if c.startswith('@') else '@' + c}/live"


def _youtube_probe(channel: str) -> dict[str, Any] | None:
    import yt_dlp
    opts = {"quiet": True, "skip_download": True, "no_warnings": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(youtube_live_url(channel), download=False)
    except Exception:
        return None
    if not info or not info.get("is_live"):
        return None
    return {"id": info.get("id"), "title": info.get("title", ""), "url": info.get("webpage_url"),
            "started_at": info.get("release_timestamp") or info.get("timestamp") or time.time(),
            "viewers": info.get("concurrent_view_count") or 0, "game": ""}


class Watcher:
    def __init__(self, db: DB, settings: Settings):
        self.db = db
        self.s = settings
        self.twitch = Twitch(settings.twitch_client_id, settings.twitch_client_secret) if settings.twitch_ready else None
        self.live: dict[int, dict[str, Any]] = {}
        self.detectors: dict[int, HypeDetector] = {}
        self.chat_logs: dict[int, deque] = {}
        self.chat_seq = 0
        self.chat_tasks: dict[int, asyncio.Task] = {}
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self.tasks: list[asyncio.Task] = []
        self.last_poll = 0.0
        self.last_error = ""

    def reconfigure(self) -> None:
        """Nach Änderung der Schlüssel in der App neuen Twitch-Client anlegen."""
        self.twitch = Twitch(self.s.twitch_client_id, self.s.twitch_client_secret) if self.s.twitch_ready else None

    # ---------- Lebenszyklus ----------
    async def start(self) -> None:
        for c in self.db.all("SELECT id, source_url FROM clips WHERE status IN ('queued','cutting')"):
            if c["source_url"]:
                self.queue.put_nowait(c["id"])
            else:
                self.db.update("clips", c["id"], {"status": "failed", "error": "Server wurde neu gestartet."})
        self.tasks = [asyncio.create_task(self._poll_loop()), asyncio.create_task(self._worker())]

    async def stop(self) -> None:
        for t in [*self.tasks, *self.chat_tasks.values()]:
            t.cancel()

    # ---------- Live-Status ----------
    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.poll()
                self.last_error = ""
            except Exception as exc:
                self.last_error = str(exc)
                log.exception("Poll fehlgeschlagen")
            await asyncio.sleep(self.s.poll_interval)

    async def poll(self) -> None:
        streamers = self.db.all("SELECT * FROM streamers WHERE active = 1")
        active_ids = {s["id"] for s in streamers}
        twitch = [s for s in streamers if s["platform"] == "twitch"]
        live_now: dict[int, dict[str, Any]] = {}
        if twitch and self.twitch:
            streams = await self.twitch.live_streams([s["channel"].lower() for s in twitch])
            for s in twitch:
                st = streams.get(s["channel"].lower())
                if st:
                    live_now[s["id"]] = {"title": st.get("title", ""), "game": st.get("game_name", ""),
                                         "viewers": st.get("viewer_count", 0), "user_id": st["user_id"],
                                         "started_at": parse_time(st["started_at"]),
                                         "url": f"https://www.twitch.tv/{s['channel'].lower()}"}
        for s in streamers:
            if s["platform"] == "youtube":
                info = await asyncio.to_thread(_youtube_probe, s["channel"])
                if info:
                    live_now[s["id"]] = info
        # Chat starten / stoppen
        for sid, info in live_now.items():
            prev = self.live.get(sid, {})
            self.live[sid] = {**prev, **info, "live": True}
            if sid not in self.chat_tasks or self.chat_tasks[sid].done():
                streamer = next(x for x in streamers if x["id"] == sid)
                self.detectors[sid] = HypeDetector(factor=self.s.hype_factor, min_rate=self.s.hype_min_rate,
                                                   cooldown=self.s.hype_cooldown)
                self.chat_tasks[sid] = asyncio.create_task(self._chat(streamer, info))
        for sid in list(self.chat_tasks):
            if sid not in live_now or sid not in active_ids:
                self.chat_tasks.pop(sid).cancel()
                self.live.pop(sid, None)
                self.chat_logs.pop(sid, None)
        self.last_poll = time.time()

    async def _chat(self, streamer: dict[str, Any], info: dict[str, Any]) -> None:
        sid = streamer["id"]
        source = twitch_chat(streamer["channel"]) if streamer["platform"] == "twitch" else youtube_chat(info["url"])
        det = self.detectors[sid]
        log_ = self.chat_logs.setdefault(sid, deque(maxlen=80))
        async for user, text, color in source:
            now = time.time()
            self.chat_seq += 1
            log_.append({"id": self.chat_seq, "t": now, "u": user, "m": text[:300], "c": color,
                         "k": bool(find_keywords(text))})
            hype = det.feed(now, text)
            if hype:
                self.chat_seq += 1
                log_.append({"id": self.chat_seq, "t": now, "u": "", "m": f"🔥 HYPE erkannt: {hype.rate}/s – Clip wird erstellt",
                             "c": "", "k": True, "sys": True})
                await self.on_hype(streamer, hype)

    def chat(self, sid: int, after: int = 0) -> list[dict[str, Any]]:
        return [m for m in self.chat_logs.get(sid, ()) if m["id"] > after]

    def status(self) -> list[dict[str, Any]]:
        now = time.time()
        out = []
        for s in self.db.all("SELECT * FROM streamers ORDER BY active DESC, channel"):
            info = self.live.get(s["id"], {})
            det = self.detectors.get(s["id"])
            out.append({**s, "live": bool(info.get("live")), "title": info.get("title", ""),
                        "game": info.get("game", ""), "viewers": info.get("viewers", 0),
                        "rate": round(det.rate(now), 2) if det and info else 0,
                        "baseline": round(det.baseline, 2) if det and info else 0,
                        "last_hype": info.get("last_hype"),
                        "video_id": info.get("id", "") if s["platform"] == "youtube" else ""})
        return out

    # ---------- Hype -> Clip ----------
    async def on_hype(self, streamer: dict[str, Any], hype: Hype) -> int:
        log.info("HYPE bei %s: %.1f/s (normal %.1f) %s", streamer["channel"], hype.rate, hype.baseline, hype.keywords)
        if streamer["id"] in self.live:
            self.live[streamer["id"]]["last_hype"] = hype.ts
        event_id = self.db.insert("events", {"streamer_id": streamer["id"], "ts": hype.ts, "rate": hype.rate,
                                             "baseline": hype.baseline, "score": hype.score,
                                             "keywords": hype.keywords, "sample": hype.sample})
        clip_id = self.db.insert("clips", {"streamer_id": streamer["id"], "event_id": event_id,
                                           "campaign_id": streamer.get("campaign_id"), "status": "queued"})
        asyncio.create_task(self._resolve_later(clip_id, streamer, hype.ts))
        return clip_id

    async def clip_now(self, streamer_id: int) -> int:
        streamer = self.db.get("streamers", streamer_id)
        if not streamer:
            raise ValueError("Streamer nicht gefunden")
        if not self.live.get(streamer_id, {}).get("live"):
            raise ValueError("Streamer ist gerade nicht live")
        det = self.detectors.get(streamer_id)
        now = time.time()
        hype = Hype(ts=now, rate=det.rate(now) if det else 0, baseline=det.baseline if det else 0, score=0,
                    keywords=["manuell"], sample=[])
        return await self.on_hype(streamer, hype)

    async def _resolve_later(self, clip_id: int, streamer: dict[str, Any], ts: float) -> None:
        await asyncio.sleep(self.s.vod_delay)
        try:
            src = await self._find_source(streamer, ts)
        except Exception as exc:
            self.db.update("clips", clip_id, {"status": "failed", "error": f"Quelle nicht gefunden: {exc}"})
            return
        self.db.update("clips", clip_id, src)
        await self.queue.put(clip_id)

    async def _find_source(self, streamer: dict[str, Any], ts: float) -> dict[str, Any]:
        before, after = self.s.clip_before, self.s.clip_after
        info = self.live.get(streamer["id"], {})
        if streamer["platform"] == "twitch":
            if not self.twitch:
                raise RuntimeError("Twitch-Schlüssel fehlen")
            user_id = info.get("user_id")
            if not user_id:
                raise RuntimeError("Stream-Infos fehlen")
            vod = await self.twitch.latest_archive(user_id)
            if vod and parse_time(vod["created_at"]) <= ts:
                offset = ts - parse_time(vod["created_at"])
                return {"source_url": vod["url"], "start_sec": offset - before, "end_sec": offset + after}
            clips = await self.twitch.clips_between(user_id, ts - 90, ts + 30)
            if clips:
                return {"source_url": clips[0]["url"], "start_sec": None, "end_sec": None}
            raise RuntimeError("Kein VOD (VODs beim Streamer aus?) und kein Community-Clip gefunden")
        # YouTube
        if not info.get("url"):
            raise RuntimeError("YouTube-Stream nicht mehr live")
        offset = ts - float(info.get("started_at") or ts)
        return {"source_url": info["url"], "start_sec": offset - before, "end_sec": offset + after}

    async def manual_clip(self, url: str, start: float | None, end: float | None,
                          streamer_id: int | None, campaign_id: int | None) -> int:
        if streamer_id and not campaign_id:
            st = self.db.get("streamers", streamer_id)
            campaign_id = st.get("campaign_id") if st else None
        clip_id = self.db.insert("clips", {"streamer_id": streamer_id, "campaign_id": campaign_id, "status": "queued",
                                           "source_url": url, "start_sec": start, "end_sec": end})
        await self.queue.put(clip_id)
        return clip_id

    async def rerender(self, clip_id: int) -> None:
        self.db.update("clips", clip_id, {"status": "queued", "error": ""})
        await self.queue.put(clip_id)

    # ---------- Schneiden ----------
    async def _worker(self) -> None:
        while True:
            clip_id = await self.queue.get()
            try:
                await self.process(clip_id)
            except Exception as exc:
                log.exception("Clip %s fehlgeschlagen", clip_id)
                self.db.update("clips", clip_id, {"status": "failed", "error": str(exc)[:500]})

    async def process(self, clip_id: int) -> None:
        clip = self.db.get("clips", clip_id)
        if not clip:
            return
        self.db.update("clips", clip_id, {"status": "cutting", "error": ""})
        streamer = self.db.get("streamers", clip["streamer_id"]) if clip["streamer_id"] else None
        campaign = self.db.get("campaigns", clip["campaign_id"]) if clip["campaign_id"] else None
        min_s = int(campaign["min_seconds"]) if campaign and campaign["min_seconds"] else 20
        max_s = int(campaign["max_seconds"]) if campaign and campaign["max_seconds"] else 60

        raw = Path(clip["raw_file"]) if clip["raw_file"] else None
        words = clip["words"] or []
        transcript = clip["transcript"] or ""
        if not raw or not raw.exists():
            # Laufender YouTube-Stream: Abschnitt ab Stream-Beginn adressieren
            live = bool(streamer and streamer["platform"] == "youtube" and clip["event_id"]
                        and clip["start_sec"] is not None)
            raw = await clipper.download_section(clip["source_url"], clip["start_sec"], clip["end_sec"],
                                                 self.s.tmp_dir / f"clip_{clip_id}_raw", live_from_start=live)
            self.db.update("clips", clip_id, {"raw_file": str(raw)})
            words, transcript = [], ""

        raw_dur = await clipper.probe_duration(raw)
        # Länge: höchstens max_s, Hype-Moment liegt am Ende -> von hinten nehmen
        length = min(raw_dur, max_s) if raw_dur else max_s
        start = max(0.0, raw_dur - length) if raw_dur else 0.0

        if not words and captions.whisper_available():
            transcript, words = await asyncio.to_thread(captions.transcribe, raw, self.s.whisper_model,
                                                        self.s.caption_language)
            self.db.update("clips", clip_id, {"words": words, "transcript": transcript})
        clip_words = [w for w in words if w["e"] > start and w["s"] < start + length]
        clip_text = " ".join(w["w"] for w in clip_words) or transcript

        hook, caption = clip["hook"], clip["caption"]
        if not hook or not caption:
            event = self.db.get("events", clip["event_id"]) if clip["event_id"] else None
            h, c = await make_hook(self.s.anthropic_api_key, (streamer or {}).get("display_name") or
                                   (streamer or {}).get("channel", ""), clip_text,
                                   (event or {}).get("keywords", []), (campaign or {}).get("required_tags", []),
                                   (campaign or {}).get("required_mention", ""))
            hook, caption = hook or h, caption or c

        out = self.s.clips_dir / f"clip_{clip_id}.mp4"
        ass = captions.build_ass(clip_words, hook, offset=start)
        await clipper.render_vertical(raw, out, start=start, duration=length, layout=clip["layout"] or "blur",
                                      ass_text=ass)
        thumb = await clipper.thumbnail(out, self.s.clips_dir / f"clip_{clip_id}.jpg")
        duration = await clipper.probe_duration(out)
        note = f"Hinweis: nur {duration:.0f}s Material (Kampagne will mind. {min_s}s)." if duration < min_s else ""
        self.db.update("clips", clip_id, {"status": "ready", "file": out.name, "thumb": thumb.name,
                                          "duration": round(duration, 1), "hook": hook, "caption": caption,
                                          "error": note})
