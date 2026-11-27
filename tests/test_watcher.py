import asyncio
import subprocess
from pathlib import Path

from app.config import Settings
from app.db import DB
from app.hype import Hype
from app.watcher import Watcher, youtube_live_url


def test_youtube_live_url():
    assert youtube_live_url("@gtaguy") == "https://www.youtube.com/@gtaguy/live"
    assert youtube_live_url("UCabc") == "https://www.youtube.com/channel/UCabc/live"


def test_hype_to_ready_clip(tmp_path: Path):
    src = tmp_path / "vod.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=22",
                    "-f", "lavfi", "-i", "sine=duration=22", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(src)], check=True)

    async def scenario():
        s = Settings()
        s.data_dir = tmp_path / "data"
        s.__post_init__()
        s.vod_delay = 0
        db = DB(s.data_dir / "t.db")
        w = Watcher(db, s)
        sid = db.insert("streamers", {"platform": "twitch", "channel": "skilmasktimmy"})

        async def fake_source(streamer, ts):
            return {"source_url": "fake", "start_sec": 0, "end_sec": 22}

        async def fake_download(url, start, end, out, live_from_start=False):
            return src

        w._find_source = fake_source
        import app.clipper as clipper
        orig = clipper.download_section
        clipper.download_section = fake_download
        worker = asyncio.create_task(w._worker())
        try:
            cid = await w.on_hype(db.get("streamers", sid), Hype(ts=1000, rate=6, baseline=0.4, score=15,
                                                                 keywords=["kekw"], sample=["KEKW"]))
            for _ in range(300):
                await asyncio.sleep(0.2)
                if db.get("clips", cid)["status"] in ("ready", "failed"):
                    break
        finally:
            worker.cancel()
            clipper.download_section = orig
        clip = db.get("clips", cid)
        assert clip["status"] == "ready", clip["error"]
        assert 21 < clip["duration"] < 23
        assert db.all("SELECT * FROM events")[0]["keywords"] == ["kekw"]
        assert (s.clips_dir / clip["file"]).exists()

    asyncio.run(scenario())
