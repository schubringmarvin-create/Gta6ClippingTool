"""Web-Server: API für die Handy-App + Auslieferung der App selbst."""
from __future__ import annotations

import asyncio
import csv
import json
import os
import hashlib
import hmac
import io
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import campaign_search, captions, clipper, link_import, push, schedule
from .config import settings
from .db import DB
from .rules import campaign_from_json, check_clip, earnings
from .watcher import Watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"
COOKIE = "gta_session"
SESSION_DAYS = 60

db = DB(settings.data_dir / "clipper.db")
EDITABLE = ("twitch_client_id", "twitch_client_secret", "anthropic_api_key")
# Hype-Regler: (min, max)
HYPE_KEYS = {"hype_factor": (2, 10), "hype_min_rate": (0.2, 20), "hype_cooldown": (60, 3600), "hype_max_per_hour": (1, 30),
             "clip_retention_days": (0, 90)}


def apply_db_settings() -> None:
    """In der App gespeicherte Werte haben Vorrang vor der .env."""
    stored = db.get_settings()
    for key in EDITABLE:
        if stored.get(key):
            setattr(settings, key, stored[key])
    if stored.get("app_password"):
        settings.app_password = stored["app_password"]
    for key in HYPE_KEYS:
        if stored.get(key):
            try:
                setattr(settings, key, float(stored[key]))
            except ValueError:
                pass


apply_db_settings()
watcher = Watcher(db, settings)
vapid = push.Vapid(settings.data_dir)
PUSH_SUBJECT = f"https://{os.environ['DOMAIN']}" if os.environ.get("DOMAIN") else "mailto:gta-clips@example.com"


async def notify_all(title: str, body: str, url: str = "/") -> dict[str, int]:
    sent = gone = 0
    for sub in db.all("SELECT * FROM push_subs"):
        try:
            code = await push.send(vapid, sub, {"title": title, "body": body, "url": url}, PUSH_SUBJECT)
        except Exception as exc:
            logging.getLogger("push").warning("Push fehlgeschlagen: %s", exc)
            continue
        if code in (404, 410):  # Abo abgelaufen
            db.delete("push_subs", sub["id"])
            gone += 1
        elif code < 300:
            sent += 1
    return {"sent": sent, "removed": gone}


async def clip_ready_push(clip_id: int) -> None:
    c = db.one("SELECT c.hook, c.duration, s.display_name, s.channel, e.rate, e.baseline FROM clips c "
               "LEFT JOIN streamers s ON s.id = c.streamer_id LEFT JOIN events e ON e.id = c.event_id WHERE c.id = ?",
               (clip_id,))
    if not c:
        return
    who = c["display_name"] or c["channel"] or "Clip"
    extra = f" · Chat {c['rate']}/s statt {c['baseline']}/s" if c.get("rate") else ""
    await notify_all(f"🎬 Neuer Clip: {who}", f"{c['hook'] or 'Fertig geschnitten'} ({round(c['duration'] or 0)} s){extra}",
                     f"/#clip-{clip_id}")


watcher.on_clip_ready = clip_ready_push


@asynccontextmanager
async def lifespan(_: FastAPI):
    await watcher.start()
    yield
    await watcher.stop()


app = FastAPI(title="GTA6 Clipping Tool", lifespan=lifespan)


# ---------- Anmeldung ----------
def _sign(exp: int) -> str:
    sig = hmac.new(settings.secret_key.encode(), f"{exp}".encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def _valid(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp, _, _sig = token.partition(".")
    if not exp.isdigit() or int(exp) < time.time():
        return False
    return hmac.compare_digest(token, _sign(int(exp)))


def auth(request: Request) -> None:
    if not settings.app_password:
        raise HTTPException(503, "APP_PASSWORD ist auf dem Server nicht gesetzt.")
    if not _valid(request.cookies.get(COOKIE)):
        raise HTTPException(401, "Nicht angemeldet")


class Login(BaseModel):
    password: str


@app.post("/api/login")
def login(body: Login, request: Request, response: Response) -> dict[str, bool]:
    if not settings.app_password:
        raise HTTPException(503, "APP_PASSWORD ist auf dem Server nicht gesetzt.")
    if not hmac.compare_digest(body.password.encode(), settings.app_password.encode()):
        time.sleep(1)
        raise HTTPException(401, "Falsches Passwort")
    exp = int(time.time()) + SESSION_DAYS * 86400
    response.set_cookie(COOKIE, _sign(exp), max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https")
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE)
    return {"ok": True}


A = [Depends(auth)]


# ---------- Status ----------
@app.get("/api/status", dependencies=A)
def status() -> dict[str, Any]:
    warnings = []
    if not settings.twitch_ready:
        warnings.append("Twitch-Schlüssel fehlen – trag sie unter Streamer → Einstellungen ein, sonst werden Twitch-Streamer nicht beobachtet.")
    if settings.enable_captions and not captions.library_available():
        warnings.append("ENABLE_CAPTIONS=1, aber faster-whisper ist nicht installiert – Clips ohne Untertitel.")
    if not settings.anthropic_api_key:
        warnings.append("Kein Anthropic-API-Key – Hooks und Captions kommen aus einer einfachen Vorlage (optional, unter Streamer → Einstellungen).")
    for tool, ok in clipper.tools_status().items():
        if not ok:
            warnings.append(f"{tool} fehlt auf dem Server.")
    if watcher.last_error:
        warnings.append(f"Letzte Abfrage fehlgeschlagen: {watcher.last_error}")
    counts = {r["status"]: r["n"] for r in db.all("SELECT status, COUNT(*) AS n FROM clips GROUP BY status")}
    disk = watcher.disk()
    if disk["free_gb"] < 3:
        warnings.append(f"Nur noch {disk['free_gb']} GB Speicher frei – alte Clips werden automatisch gelöscht.")
    return {"streamers": watcher.status(), "warnings": warnings, "last_poll": watcher.last_poll, "disk": disk,
            "clips": counts, "events": db.all(
                "SELECT e.*, s.channel FROM events e LEFT JOIN streamers s ON s.id = e.streamer_id "
                "ORDER BY e.ts DESC LIMIT 15")}


# ---------- Einstellungen ----------
class SettingsIn(BaseModel):
    twitch_client_id: str | None = None
    twitch_client_secret: str | None = None
    anthropic_api_key: str | None = None
    new_password: str | None = None
    hype_factor: float | None = None
    hype_min_rate: float | None = None
    hype_cooldown: float | None = None
    hype_max_per_hour: float | None = None
    clip_retention_days: float | None = None


def _mask(value: str) -> str:
    return "" if not value else ("•" * 6 + value[-4:] if len(value) > 8 else "•" * 6)


@app.get("/api/settings", dependencies=A)
def get_settings() -> dict[str, Any]:
    return {"twitch_client_id": settings.twitch_client_id,
            "twitch_client_secret": _mask(settings.twitch_client_secret),
            "anthropic_api_key": _mask(settings.anthropic_api_key),
            "twitch_ready": settings.twitch_ready,
            **{k: getattr(settings, k) for k in HYPE_KEYS}, "disk": watcher.disk(),
            "push_key": vapid.public_b64}


@app.put("/api/settings", dependencies=A)
async def put_settings(body: SettingsIn, response: Response) -> dict[str, Any]:
    for key in EDITABLE:
        value = getattr(body, key)
        if value is None or value.startswith("•"):
            continue  # leer gelassen oder maskiert = unverändert
        db.set_setting(key, value.strip())
        setattr(settings, key, value.strip())
    if body.new_password:
        if len(body.new_password) < 8:
            raise HTTPException(400, "Passwort muss mindestens 8 Zeichen haben")
        db.set_setting("app_password", body.new_password)
        settings.app_password = body.new_password
    for key, (lo, hi) in HYPE_KEYS.items():
        value = getattr(body, key)
        if value is None:
            continue
        if not lo <= value <= hi:
            raise HTTPException(400, f"Wert für {key} muss zwischen {lo} und {hi} liegen")
        db.set_setting(key, str(value))
        setattr(settings, key, float(value))
    watcher.apply_hype_settings()
    if not any(getattr(body, k) for k in EDITABLE):
        return get_settings()  # nur Hype-Regler geändert: keine Twitch-Prüfung nötig
    watcher.reconfigure()
    if watcher.twitch:
        try:
            await watcher.twitch._auth()  # prüft, ob Client-ID und Secret stimmen
        except Exception:
            raise HTTPException(400, "Gespeichert, aber Twitch lehnt die Schlüssel ab – bitte Client-ID und Secret prüfen.")
        try:
            await watcher.poll()
            watcher.last_error = ""
        except Exception as exc:
            watcher.last_error = str(exc)
    return get_settings()


# ---------- Updates ----------
def _update_status() -> dict[str, Any]:
    f = settings.data_dir / "update-status.json"
    try:
        status = json.loads(f.read_text())
    except (OSError, ValueError):
        status = {"state": "unknown", "message": "", "version": ""}
    status["requested"] = (settings.data_dir / "update-request").exists()
    return status


@app.get("/api/update", dependencies=A)
def update_status() -> dict[str, Any]:
    return _update_status()


@app.post("/api/update", dependencies=A)
def request_update() -> dict[str, Any]:
    (settings.data_dir / "update-request").write_text(str(time.time()))
    return _update_status()


# ---------- Zeitplan & Auswertung ----------
@app.get("/api/schedule", dependencies=A)
async def get_schedule(refresh: bool = False) -> list[dict[str, Any]]:
    if refresh:
        await watcher.backfill_vods()
    out = []
    since = time.time() - 28 * 86400
    for s in db.all("SELECT * FROM streamers ORDER BY channel"):
        sessions = db.all("SELECT * FROM live_sessions WHERE streamer_id=? AND (ended_at IS NULL OR ended_at > ?)",
                          (s["id"], since))
        heat = schedule.heatmap(sessions)
        hours = sum(((x["ended_at"] or time.time()) - max(x["started_at"], since)) for x in sessions) / 3600
        out.append({"id": s["id"], "channel": s["channel"], "name": s["display_name"] or s["channel"],
                    "live": bool(watcher.live.get(s["id"], {}).get("live")), "heat": heat,
                    "summary": schedule.summary(heat), "next": schedule.next_likely(heat),
                    "streams": len(sessions), "hours_per_week": round(hours / 4, 1)})
    return out


@app.get("/api/stats", dependencies=A)
def stats() -> dict[str, Any]:
    rows = _tracker_rows()
    camps = {c["id"]: c for c in db.all("SELECT * FROM campaigns")}

    def group(key: str) -> list[dict[str, Any]]:
        g: dict[str, dict[str, Any]] = {}
        for r in rows:
            name = r.get(key) or "ohne"
            e = g.setdefault(name, {"name": name, "posts": 0, "views": 0, "estimate": 0.0, "paid": 0.0})
            e["posts"] += 1
            e["views"] += int(r["views"] or 0)
            e["estimate"] += r["estimate"]
            e["paid"] += float(r["paid"] or 0)
        for e in g.values():
            e["estimate"] = round(e["estimate"], 2)
            e["paid"] = round(e["paid"], 2)
            e["avg_views"] = round(e["views"] / e["posts"]) if e["posts"] else 0
        return sorted(g.values(), key=lambda e: (e["estimate"], e["views"]), reverse=True)

    clip_counts = {r["channel"] or "ohne": r["n"] for r in db.all(
        "SELECT s.channel, COUNT(*) AS n FROM clips c LEFT JOIN streamers s ON s.id = c.streamer_id "
        "WHERE c.status='ready' GROUP BY s.channel")}
    by_streamer = group("channel")
    for e in by_streamer:
        e["clips"] = clip_counts.get(e["name"], 0)
    top = sorted(rows, key=lambda r: int(r["views"] or 0), reverse=True)[:5]
    week = time.time() - 7 * 86400
    return {"by_streamer": by_streamer, "by_campaign": group("campaign_name"),
            "top": [{"id": r["id"], "channel": r["channel"], "platform": r["platform"], "views": r["views"],
                     "estimate": r["estimate"], "url": r["url"], "thumb_url": r["thumb_url"], "hook": r["hook"]}
                    for r in top if int(r["views"] or 0) > 0],
            "week": {"posts": sum(1 for r in rows if (r["posted_at"] or 0) > week),
                     "views": sum(int(r["views"] or 0) for r in rows if (r["posted_at"] or 0) > week)},
            "campaigns_known": len(camps)}


# ---------- Push ----------
class PushSub(BaseModel):
    endpoint: str
    keys: dict[str, str]


@app.get("/api/push", dependencies=A)
def push_info() -> dict[str, Any]:
    return {"key": vapid.public_b64, "subscriptions": len(db.all("SELECT id FROM push_subs"))}


@app.post("/api/push/subscribe", dependencies=A)
def push_subscribe(body: PushSub) -> dict[str, Any]:
    if not body.endpoint.startswith("https://") or not body.keys.get("p256dh") or not body.keys.get("auth"):
        raise HTTPException(400, "Ungültiges Push-Abo")
    db.delete_where("push_subs", "endpoint", body.endpoint)
    db.insert("push_subs", {"endpoint": body.endpoint, "p256dh": body.keys["p256dh"], "auth": body.keys["auth"],
                            "created_at": time.time()})
    return push_info()


@app.post("/api/push/unsubscribe", dependencies=A)
def push_unsubscribe(body: PushSub) -> dict[str, Any]:
    db.delete_where("push_subs", "endpoint", body.endpoint)
    return push_info()


@app.post("/api/push/test", dependencies=A)
async def push_test() -> dict[str, int]:
    return await notify_all("GTA Clips", "Benachrichtigungen funktionieren 🎉", "/")


@app.post("/api/cleanup", dependencies=A)
async def cleanup_now() -> dict[str, Any]:
    removed = await asyncio.to_thread(watcher.cleanup)
    return {"removed": removed, "disk": watcher.disk()}


# ---------- Streamer ----------
class StreamerIn(BaseModel):
    platform: str = "twitch"
    channel: str
    display_name: str = ""
    active: bool = True
    campaign_id: int | None = None


class StreamerPatch(BaseModel):
    display_name: str | None = None
    active: bool | None = None
    campaign_id: int | None = None


@app.get("/api/streamers", dependencies=A)
def list_streamers() -> list[dict[str, Any]]:
    return db.all("SELECT * FROM streamers ORDER BY channel")


@app.post("/api/streamers", dependencies=A)
async def add_streamer(body: StreamerIn) -> dict[str, Any]:
    if body.platform not in {"twitch", "youtube"}:
        raise HTTPException(400, "Plattform muss twitch oder youtube sein")
    channel = body.channel.strip()
    if body.platform == "twitch":
        channel = channel.rstrip("/").split("/")[-1].lstrip("@").lower()
    if not channel:
        raise HTTPException(400, "Kanal fehlt")
    if db.one("SELECT id FROM streamers WHERE platform = ? AND channel = ?", (body.platform, channel)):
        raise HTTPException(409, "Streamer ist schon auf der Liste")
    sid = db.insert("streamers", {"platform": body.platform, "channel": channel,
                                  "display_name": body.display_name or channel, "active": int(body.active),
                                  "campaign_id": body.campaign_id})
    return db.get("streamers", sid)  # type: ignore[return-value]


@app.patch("/api/streamers/{sid}", dependencies=A)
def patch_streamer(sid: int, body: StreamerPatch) -> dict[str, Any]:
    values = body.model_dump(exclude_unset=True)
    if "active" in values:
        values["active"] = int(values["active"])
    db.update("streamers", sid, values)
    return db.get("streamers", sid) or {}


@app.delete("/api/streamers/{sid}", dependencies=A)
def delete_streamer(sid: int) -> dict[str, bool]:
    db.delete("streamers", sid)
    return {"ok": True}


@app.post("/api/streamers/{sid}/clip-now", dependencies=A)
async def clip_now(sid: int) -> dict[str, Any]:
    try:
        return {"clip_id": await watcher.clip_now(sid)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/chat", dependencies=A)
def chat_all(after: int = 0) -> dict[str, Any]:
    """Neue Chat-Nachrichten aller Live-Streamer seit Nachricht-ID `after`."""
    out = {str(sid): watcher.chat(sid, after) for sid in list(watcher.chat_logs)}
    return {"chats": out, "last": watcher.chat_seq}


@app.post("/api/poll", dependencies=A)
async def poll_now() -> dict[str, bool]:
    await watcher.poll()
    return {"ok": True}


# ---------- Kampagnen ----------
class CampaignIn(BaseModel):
    name: str
    streamer: str = ""
    platforms: list[str] = []
    rate_per_1k: float = 0
    min_views: int = 0
    max_payout: float = 0
    min_seconds: int = 20
    max_seconds: int = 60
    required_tags: list[str] = []
    required_mention: str = ""
    rules: str = ""
    clips_allowed: bool = True
    url: str = ""
    twitch_channel: str = ""


def _link_streamer(channel: str, cid: int, display: str = "") -> None:
    channel = channel.strip().rstrip("/").split("/")[-1].lstrip("@").lower()
    if not channel:
        return
    existing = db.one("SELECT id FROM streamers WHERE platform='twitch' AND channel=?", (channel,))
    if existing:
        db.update("streamers", existing["id"], {"campaign_id": cid, "active": 1})
    else:
        db.insert("streamers", {"platform": "twitch", "channel": channel, "active": 1, "campaign_id": cid,
                                "display_name": display or channel})


def _campaign_values(body: CampaignIn) -> dict[str, Any]:
    v = body.model_dump(exclude={"twitch_channel"})
    v["clips_allowed"] = int(v["clips_allowed"])
    v["platforms"] = [p.strip().lower() for p in v["platforms"] if p.strip()]
    v["required_tags"] = [t.strip() for t in v["required_tags"] if t.strip()]
    return v


@app.get("/api/campaigns", dependencies=A)
def list_campaigns() -> list[dict[str, Any]]:
    return db.all("SELECT * FROM campaigns ORDER BY rate_per_1k DESC, name")


@app.post("/api/campaigns", dependencies=A)
async def add_campaign(body: CampaignIn) -> dict[str, Any]:
    cid = db.insert("campaigns", _campaign_values(body))
    if body.twitch_channel:
        _link_streamer(body.twitch_channel, cid, body.streamer)
        try:
            await watcher.poll()
        except Exception as exc:
            watcher.last_error = str(exc)
    return db.get("campaigns", cid)  # type: ignore[return-value]


class LinkIn(BaseModel):
    url: str


@app.post("/api/campaigns/from-link", dependencies=A)
async def campaign_from_link(body: LinkIn) -> dict[str, Any]:
    url = body.url.strip()
    if not url.startswith("http"):
        raise HTTPException(400, "Bitte einen vollständigen Link einfügen (beginnt mit https://)")
    try:
        return await link_import.campaign_from_link(url, settings.anthropic_api_key)
    except Exception as exc:
        raise HTTPException(400, f"Seite konnte nicht geladen werden: {exc}")


@app.put("/api/campaigns/{cid}", dependencies=A)
def put_campaign(cid: int, body: CampaignIn) -> dict[str, Any]:
    db.update("campaigns", cid, _campaign_values(body))
    return db.get("campaigns", cid) or {}


@app.delete("/api/campaigns/{cid}", dependencies=A)
def delete_campaign(cid: int) -> dict[str, bool]:
    db.delete("campaigns", cid)
    return {"ok": True}


@app.post("/api/campaigns/import", dependencies=A)
async def import_campaigns(request: Request) -> dict[str, int]:
    data = await request.json()
    items = data.get("kampagnen") or data.get("campaigns") or [] if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise HTTPException(400, "Erwarte eine Liste von Kampagnen")
    n = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        cid = db.insert("campaigns", campaign_from_json(item))
        n += 1
        # Mit Twitch-Kanal: Streamer gleich anlegen bzw. mit der Kampagne verknüpfen
        if item.get("twitch_channel"):
            _link_streamer(str(item["twitch_channel"]), cid, str(item.get("streamer") or ""))
    return {"imported": n}


# ---------- Kampagnen-Suche ----------
SEARCH: dict[str, Any] = {"status": "idle"}


def _load_last_search() -> None:
    raw = db.get_settings().get("campaign_search_last")
    if raw:
        try:
            SEARCH.update(json.loads(raw))
            if SEARCH.get("status") == "running":
                SEARCH["status"] = "error"
                SEARCH["error"] = "Suche wurde durch einen Neustart unterbrochen."
        except ValueError:
            pass


_load_last_search()


async def _search_job() -> None:
    known = [c["name"] for c in db.all("SELECT name FROM campaigns")]
    try:
        result = await campaign_search.run_search(settings, watcher.twitch, known)
        SEARCH.update({"status": "done", "error": "", "finished_at": time.time(), **result})
    except Exception as exc:
        logging.getLogger("campaign_search").exception("Suche fehlgeschlagen")
        SEARCH.update({"status": "error", "error": str(exc)[:500], "finished_at": time.time()})
    db.set_setting("campaign_search_last", json.dumps(SEARCH, ensure_ascii=False))


@app.get("/api/campaign-search", dependencies=A)
def get_search() -> dict[str, Any]:
    existing = {(s["channel"] or "").lower() for s in db.all("SELECT channel FROM streamers WHERE platform='twitch'")}
    out = dict(SEARCH)
    out["results"] = [{**r, "already_added": r.get("twitch_channel") in existing} for r in SEARCH.get("results", [])]
    out["has_api_key"] = bool(settings.anthropic_api_key)
    return out


@app.post("/api/campaign-search", dependencies=A)
async def start_search() -> dict[str, Any]:
    if not settings.anthropic_api_key:
        raise HTTPException(400, "Für die Suche wird ein Anthropic-API-Key gebraucht – trag ihn unter Streamer → Einstellungen ein.")
    if SEARCH.get("status") != "running":
        SEARCH.clear()
        SEARCH.update({"status": "running", "started_at": time.time(), "results": [], "live_gta6": []})
        asyncio.create_task(_search_job())
    return get_search()


@app.get("/api/live-gta6", dependencies=A)
async def live_gta6() -> dict[str, Any]:
    """Kostenlos über die Twitch-API: wer streamt gerade GTA 6, und ist er schon auf meiner Liste?"""
    if not watcher.twitch:
        return {"twitch_ready": False, "streams": []}
    try:
        streams = await campaign_search.live_gta6_streams(watcher.twitch, limit=40)
    except Exception as exc:
        raise HTTPException(502, f"Twitch-Abfrage fehlgeschlagen: {exc}")
    mine = {s["channel"]: s for s in db.all(
        "SELECT s.channel, s.campaign_id, k.name AS campaign_name FROM streamers s "
        "LEFT JOIN campaigns k ON k.id = s.campaign_id WHERE s.platform = 'twitch'")}
    camps = db.all("SELECT id, name, streamer FROM campaigns")
    for st in streams:
        m = mine.get(st["channel"])
        st["on_list"] = bool(m)
        st["campaign_name"] = (m or {}).get("campaign_name") or ""
        if not st["campaign_name"]:  # Kampagne mit passendem Streamer-Namen?
            hit = next((c for c in camps if c["streamer"] and c["streamer"].lower().replace(" ", "") in
                        (st["channel"], st["name"].lower())), None)
            st["campaign_match"] = hit["name"] if hit else ""
            st["campaign_match_id"] = hit["id"] if hit else None
    return {"twitch_ready": True, "streams": streams}


class AdoptIn(BaseModel):
    index: int


@app.post("/api/campaign-search/adopt", dependencies=A)
async def adopt(body: AdoptIn) -> dict[str, Any]:
    results = SEARCH.get("results") or []
    if not 0 <= body.index < len(results):
        raise HTTPException(404, "Ergebnis nicht gefunden")
    r = results[body.index]
    cid = db.insert("campaigns", {
        "name": r["name"], "streamer": r.get("streamer") or r["twitch_channel"], "platforms": r.get("platforms", []),
        "rate_per_1k": r.get("rate_per_1k", 0), "min_views": r.get("min_views", 0), "max_payout": r.get("max_payout", 0),
        "min_seconds": r.get("min_seconds", 20), "max_seconds": r.get("max_seconds", 60),
        "required_tags": r.get("required_tags", []), "required_mention": r.get("required_mention", ""),
        "rules": r.get("rules", ""), "clips_allowed": 1, "url": r.get("url", "")})
    existing = db.one("SELECT id FROM streamers WHERE platform='twitch' AND channel=?", (r["twitch_channel"],))
    if existing:
        db.update("streamers", existing["id"], {"campaign_id": cid, "active": 1})
    else:
        db.insert("streamers", {"platform": "twitch", "channel": r["twitch_channel"],
                                "display_name": r.get("streamer") or r["twitch_channel"], "active": 1, "campaign_id": cid})
    try:
        await watcher.poll()
    except Exception as exc:
        watcher.last_error = str(exc)
    return {"campaign_id": cid}


# ---------- Clips ----------
def _clip_out(c: dict[str, Any]) -> dict[str, Any]:
    c = dict(c)
    c.pop("words", None)
    c["video_url"] = f"/media/{c['file']}" if c.get("file") else ""
    c["thumb_url"] = f"/media/{c['thumb']}" if c.get("thumb") else ""
    return c


@app.get("/api/clips", dependencies=A)
def list_clips(status: str | None = None, sort: str = "new") -> list[dict[str, Any]]:
    sql = ("SELECT c.*, s.channel, s.display_name, k.name AS campaign_name, "
           "e.rate AS hype_rate, e.baseline AS hype_baseline, e.score AS hype_score, e.sample AS hype_sample, "
           "e.keywords AS hype_keywords, "
           "(SELECT COUNT(*) FROM posts p WHERE p.clip_id = c.id) AS post_count "
           "FROM clips c LEFT JOIN streamers s ON s.id = c.streamer_id "
           "LEFT JOIN campaigns k ON k.id = c.campaign_id LEFT JOIN events e ON e.id = c.event_id")
    where, args = "", ()
    if status == "posted":
        where = " WHERE (SELECT COUNT(*) FROM posts p WHERE p.clip_id = c.id) > 0"
    elif status == "new":
        where = " WHERE c.status = 'ready' AND c.seen = 0"
    elif status:
        where, args = " WHERE c.status = ?", (status,)
    order = " ORDER BY COALESCE(e.score, 0) DESC, c.id DESC" if sort == "best" else " ORDER BY c.id DESC"
    rows = db.all(sql + where + order + " LIMIT 150", args)
    for r in rows:
        for k in ("hype_sample", "hype_keywords"):
            try:
                r[k] = json.loads(r[k]) if r.get(k) else []
            except ValueError:
                r[k] = []
    return [_clip_out(r) for r in rows]


class ManualClip(BaseModel):
    url: str
    start: float | None = None
    end: float | None = None
    streamer_id: int | None = None
    campaign_id: int | None = None


@app.post("/api/clips", dependencies=A)
async def manual_clip(body: ManualClip) -> dict[str, Any]:
    if not body.url.startswith("http"):
        raise HTTPException(400, "Bitte einen Link angeben")
    if (body.start is None) != (body.end is None) or (body.start is not None and body.end <= body.start):  # type: ignore[operator]
        raise HTTPException(400, "Start und Ende angeben (Ende nach Start) oder beides leer lassen")
    return {"clip_id": await watcher.manual_clip(body.url, body.start, body.end, body.streamer_id, body.campaign_id)}


class ClipPatch(BaseModel):
    hook: str | None = None
    caption: str | None = None
    campaign_id: int | None = None
    layout: str | None = None
    trim_start: float | None = None
    trim_end: float | None = None
    seen: int | None = None
    captions: int | None = None


@app.patch("/api/clips/{cid}", dependencies=A)
def patch_clip(cid: int, body: ClipPatch) -> dict[str, Any]:
    values = body.model_dump(exclude_unset=True)
    if values.get("layout") not in (None, "blur", "crop"):
        raise HTTPException(400, "Layout muss blur oder crop sein")
    db.update("clips", cid, values)
    return _clip_out(db.get("clips", cid) or {})


@app.post("/api/clips/{cid}/rerender", dependencies=A)
async def rerender(cid: int) -> dict[str, bool]:
    clip = db.get("clips", cid)
    if not clip or not (clip["raw_file"] or clip["source_url"]):
        raise HTTPException(400, "Clip kann nicht neu geschnitten werden")
    await watcher.rerender(cid)
    return {"ok": True}


@app.delete("/api/clips/{cid}", dependencies=A)
def delete_clip(cid: int) -> dict[str, bool]:
    clip = db.get("clips", cid)
    if clip:
        watcher.remove_clip_files(clip)
        db.delete("clips", cid)
    return {"ok": True}


class CheckIn(BaseModel):
    platform: str
    caption: str


@app.post("/api/clips/{cid}/check", dependencies=A)
def check(cid: int, body: CheckIn) -> dict[str, Any]:
    clip = db.get("clips", cid)
    if not clip:
        raise HTTPException(404, "Clip nicht gefunden")
    campaign = db.get("campaigns", clip["campaign_id"]) if clip["campaign_id"] else None
    return check_clip(clip, campaign, body.platform, body.caption)


class PostIn(BaseModel):
    platform: str
    caption: str
    url: str = ""


@app.post("/api/clips/{cid}/posts", dependencies=A)
def add_post(cid: int, body: PostIn) -> dict[str, Any]:
    clip = db.get("clips", cid)
    if not clip:
        raise HTTPException(404, "Clip nicht gefunden")
    campaign = db.get("campaigns", clip["campaign_id"]) if clip["campaign_id"] else None
    result = check_clip(clip, campaign, body.platform, body.caption)
    if not result["allowed"]:
        raise HTTPException(400, "Regel-Check nicht bestanden: " +
                            "; ".join(c["msg"] for c in result["checks"] if not c["ok"]))
    pid = db.insert("posts", {"clip_id": cid, "platform": body.platform, "url": body.url, "caption": body.caption,
                              "posted_at": time.time()})
    db.update("clips", cid, {"caption": body.caption})
    return db.get("posts", pid) or {}


# ---------- Tracker ----------
class PostPatch(BaseModel):
    views: int | None = None
    url: str | None = None
    paid: float | None = None


@app.patch("/api/posts/{pid}", dependencies=A)
def patch_post(pid: int, body: PostPatch) -> dict[str, Any]:
    db.update("posts", pid, body.model_dump(exclude_unset=True))
    return db.get("posts", pid) or {}


@app.delete("/api/posts/{pid}", dependencies=A)
def delete_post(pid: int) -> dict[str, bool]:
    db.delete("posts", pid)
    return {"ok": True}


def _tracker_rows() -> list[dict[str, Any]]:
    rows = db.all("SELECT p.*, c.campaign_id, c.hook, c.thumb, s.channel, k.name AS campaign_name "
                  "FROM posts p JOIN clips c ON c.id = p.clip_id LEFT JOIN streamers s ON s.id = c.streamer_id "
                  "LEFT JOIN campaigns k ON k.id = c.campaign_id ORDER BY p.posted_at DESC")
    camps = {c["id"]: c for c in db.all("SELECT * FROM campaigns")}
    for r in rows:
        r["estimate"] = earnings(int(r["views"] or 0), camps.get(r["campaign_id"]))
        r["thumb_url"] = f"/media/{r['thumb']}" if r.get("thumb") else ""
    return rows


@app.get("/api/tracker", dependencies=A)
def tracker() -> dict[str, Any]:
    rows = _tracker_rows()
    return {"posts": rows, "total_views": sum(int(r["views"] or 0) for r in rows),
            "total_estimate": round(sum(r["estimate"] for r in rows), 2),
            "total_paid": round(sum(float(r["paid"] or 0) for r in rows), 2)}


@app.get("/api/tracker.csv", dependencies=A)
def tracker_csv() -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Datum", "Streamer", "Kampagne", "Plattform", "Link", "Aufrufe", "Schätzung €", "Ausgezahlt €"])
    for r in _tracker_rows():
        w.writerow([time.strftime("%Y-%m-%d %H:%M", time.localtime(r["posted_at"] or 0)), r["channel"] or "",
                    r["campaign_name"] or "", r["platform"], r["url"], r["views"], r["estimate"], r["paid"] or ""])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=tracker.csv"})


# ---------- Dateien ----------
@app.get("/media/{name}", dependencies=A)
def media(name: str) -> FileResponse:
    path = (settings.clips_dir / name).resolve()
    if path.parent != settings.clips_dir.resolve() or not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
