import asyncio
import subprocess
import time
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app import schedule
from app.config import Settings
from app.db import DB


def ts(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=schedule.TZ).timestamp()


def test_parse_duration():
    assert schedule.parse_duration("3h2m10s") == 3 * 3600 + 130
    assert schedule.parse_duration("45m") == 2700


def test_heatmap_summary_next():
    # 4 Wochen lang Mo, Mi, Fr jeweils 18-23 Uhr (Berliner Zeit)
    now = ts(2026, 11, 27, 12)  # Freitag Mittag
    sessions = []
    for week in range(4):
        monday = datetime(2026, 11, 2, tzinfo=schedule.TZ) + timedelta(weeks=week)
        for d in (0, 2, 4):
            start = (monday + timedelta(days=d, hours=18)).timestamp()
            if start < now:
                sessions.append({"started_at": start, "ended_at": start + 5 * 3600})
    heat = schedule.heatmap(sessions, now=now)
    assert heat[0][18] == 1.0 and heat[0][22] == 1.0 and heat[0][23] == 0
    assert heat[1][18] == 0
    assert schedule.summary(heat) == "meist Mo, Mi, Fr · 18–23 Uhr"
    nxt = datetime.fromtimestamp(schedule.next_likely(heat, now=now), schedule.TZ)
    assert (nxt.weekday(), nxt.hour) == (4, 18)  # heute 18 Uhr
    assert schedule.summary([[0] * 24 for _ in range(7)]) == "noch keine Daten"


class FakeTwitch:
    async def users(self, logins):
        return {l: {"id": "7"} for l in logins}

    async def archives(self, uid, first=20):
        return [{"created_at": "2026-11-20T17:00:00Z", "duration": "4h0m0s"},
                {"created_at": "2026-11-22T17:00:00Z", "duration": "2h30m0s"}]


def test_sessions_and_backfill(tmp_path):
    from app.watcher import Watcher
    s = Settings()
    s.data_dir = tmp_path / "d"
    s.__post_init__()
    db = DB(s.data_dir / "t.db")
    w = Watcher(db, s)
    w.twitch = FakeTwitch()
    sid = db.insert("streamers", {"platform": "twitch", "channel": "darealboeshi"})
    start = time.time() - 3600
    w._record_sessions({sid: {"started_at": start}}, {sid})
    w._record_sessions({sid: {"started_at": start + 5}}, {sid})  # gleicher Stream -> kein Duplikat
    assert len(db.all("SELECT * FROM live_sessions")) == 1
    w._record_sessions({}, {sid})  # offline -> beendet
    assert db.one("SELECT ended_at FROM live_sessions")["ended_at"] is not None
    added = asyncio.run(w.backfill_vods())
    assert added == 2 and asyncio.run(w.backfill_vods()) == 0


def test_on_demand_captions_never_break_clip(tmp_path, monkeypatch):
    from app import captions
    from app.watcher import Watcher
    import app.clipper as clipper
    src = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=21",
                    "-f", "lavfi", "-i", "sine=duration=21", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(src)], check=True)
    s = Settings()
    s.data_dir = tmp_path / "d"
    s.__post_init__()
    db = DB(s.data_dir / "t.db")
    w = Watcher(db, s)

    async def fake_download(url, start, end, out, live_from_start=False):
        return src
    monkeypatch.setattr(clipper, "download_section", fake_download)
    monkeypatch.setattr(captions, "library_available", lambda: True)
    calls = []

    def fake_transcribe(path, model, lang, keep):
        calls.append(keep)
        return "das ist komplett eskaliert", [{"w": w_, "s": 2 + i * 0.4, "e": 2.35 + i * 0.4}
                                               for i, w_ in enumerate("das ist komplett eskaliert".split())]
    monkeypatch.setattr(captions, "transcribe", fake_transcribe)
    cid = db.insert("clips", {"status": "queued", "source_url": "x", "captions": 1})
    asyncio.run(w.process(cid))
    c = db.get("clips", cid)
    assert c["status"] == "ready" and c["words"] and calls == [False]  # Modell wieder freigegeben

    def broken(*a):
        raise RuntimeError("kein Modell")
    monkeypatch.setattr(captions, "transcribe", broken)
    cid2 = db.insert("clips", {"status": "queued", "source_url": "x", "captions": 1})
    asyncio.run(w.process(cid2))
    c2 = db.get("clips", cid2)
    assert c2["status"] == "ready" and "Untertitel fehlgeschlagen" in c2["error"]


def test_stats_and_schedule_endpoints():
    from app import main
    with TestClient(main.app) as c:
        c.post("/api/login", json={"password": main.settings.app_password})
        sid = main.db.insert("streamers", {"platform": "twitch", "channel": "statsguy"})
        k = main.db.insert("campaigns", {"name": "Stats Kampagne", "rate_per_1k": 2})
        cid = main.db.insert("clips", {"status": "ready", "streamer_id": sid, "campaign_id": k, "file": "s.mp4"})
        main.db.insert("posts", {"clip_id": cid, "platform": "tiktok", "views": 12000, "posted_at": time.time()})
        st = c.get("/api/stats").json()
        row = next(e for e in st["by_streamer"] if e["name"] == "statsguy")
        assert row["views"] == 12000 and row["estimate"] == 24.0 and row["clips"] == 1
        assert any(e["name"] == "Stats Kampagne" for e in st["by_campaign"])
        assert st["top"][0]["views"] == 12000 and st["week"]["posts"] >= 1
        sc = c.get("/api/schedule").json()
        assert any(x["channel"] == "statsguy" and len(x["heat"]) == 7 for x in sc)
