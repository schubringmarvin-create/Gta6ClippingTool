"""Web-Server: API für die Handy-App + Auslieferung der App selbst."""
from __future__ import annotations

import csv
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

from . import captions, clipper
from .config import settings
from .db import DB
from .rules import campaign_from_json, check_clip, earnings
from .watcher import Watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"
COOKIE = "gta_session"
SESSION_DAYS = 60

db = DB(settings.data_dir / "clipper.db")
watcher = Watcher(db, settings)


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
        warnings.append("Twitch-Schlüssel fehlen (TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET) – Twitch-Streamer werden nicht beobachtet.")
    if settings.enable_captions and not captions.whisper_available():
        warnings.append("ENABLE_CAPTIONS=1, aber faster-whisper ist nicht installiert – Clips ohne Untertitel.")
    if not settings.anthropic_api_key:
        warnings.append("Kein ANTHROPIC_API_KEY – Hooks und Captions kommen aus einer einfachen Vorlage.")
    for tool, ok in clipper.tools_status().items():
        if not ok:
            warnings.append(f"{tool} fehlt auf dem Server.")
    if watcher.last_error:
        warnings.append(f"Letzte Abfrage fehlgeschlagen: {watcher.last_error}")
    counts = {r["status"]: r["n"] for r in db.all("SELECT status, COUNT(*) AS n FROM clips GROUP BY status")}
    return {"streamers": watcher.status(), "warnings": warnings, "last_poll": watcher.last_poll,
            "clips": counts, "events": db.all(
                "SELECT e.*, s.channel FROM events e LEFT JOIN streamers s ON s.id = e.streamer_id "
                "ORDER BY e.ts DESC LIMIT 15")}


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


def _campaign_values(body: CampaignIn) -> dict[str, Any]:
    v = body.model_dump()
    v["clips_allowed"] = int(v["clips_allowed"])
    v["platforms"] = [p.strip().lower() for p in v["platforms"] if p.strip()]
    v["required_tags"] = [t.strip() for t in v["required_tags"] if t.strip()]
    return v


@app.get("/api/campaigns", dependencies=A)
def list_campaigns() -> list[dict[str, Any]]:
    return db.all("SELECT * FROM campaigns ORDER BY rate_per_1k DESC, name")


@app.post("/api/campaigns", dependencies=A)
def add_campaign(body: CampaignIn) -> dict[str, Any]:
    return db.get("campaigns", db.insert("campaigns", _campaign_values(body)))  # type: ignore[return-value]


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
        if isinstance(item, dict):
            db.insert("campaigns", campaign_from_json(item))
            n += 1
    return {"imported": n}


# ---------- Clips ----------
def _clip_out(c: dict[str, Any]) -> dict[str, Any]:
    c = dict(c)
    c.pop("words", None)
    c["video_url"] = f"/media/{c['file']}" if c.get("file") else ""
    c["thumb_url"] = f"/media/{c['thumb']}" if c.get("thumb") else ""
    return c


@app.get("/api/clips", dependencies=A)
def list_clips(status: str | None = None) -> list[dict[str, Any]]:
    sql = ("SELECT c.*, s.channel, s.display_name, k.name AS campaign_name, "
           "(SELECT COUNT(*) FROM posts p WHERE p.clip_id = c.id) AS post_count "
           "FROM clips c LEFT JOIN streamers s ON s.id = c.streamer_id "
           "LEFT JOIN campaigns k ON k.id = c.campaign_id")
    rows = db.all(sql + (" WHERE c.status = ?" if status else "") + " ORDER BY c.id DESC LIMIT 100",
                  (status,) if status else ())
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
        for name in (clip["file"], clip["thumb"]):
            if name:
                (settings.clips_dir / name).unlink(missing_ok=True)
        if clip["raw_file"]:
            Path(clip["raw_file"]).unlink(missing_ok=True)
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
