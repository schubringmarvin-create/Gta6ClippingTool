import asyncio
import base64
import hashlib
import hmac
import json
import sqlite3
import struct
import subprocess
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

from app import push
from app.config import Settings
from app.db import DB


def _h(k, d):
    return hmac.new(k, d, hashlib.sha256).digest()


def test_push_encrypt_roundtrip():
    """Verschlüsseln wie der Server, entschlüsseln wie ein Handy (RFC 8291)."""
    ua_key = ec.generate_private_key(ec.SECP256R1())
    ua_pub = ua_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = b"0123456789abcdef"
    body = push.encrypt(b'{"title":"Neuer Clip"}', push.b64u(ua_pub), push.b64u(auth))
    salt, rs, idlen = body[:16], struct.unpack("!I", body[16:20])[0], body[20]
    as_pub, ct = body[21:21 + idlen], body[21 + idlen:]
    assert rs == 4096 and idlen == 65
    shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = _h(_h(auth, shared), b"WebPush: info\x00" + ua_pub + as_pub + b"\x01")
    prk = _h(salt, ikm)
    cek = _h(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _h(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    plain = AESGCM(cek).decrypt(nonce, ct, None)
    assert plain == b'{"title":"Neuer Clip"}\x02'


def test_vapid_jwt_signature(tmp_path):
    v = push.Vapid(tmp_path)
    hdr = v.auth_header("https://web.push.apple.com/abc", "https://example.sslip.io")
    token = hdr.split("t=")[1].split(",")[0]
    h, c, sig = token.split(".")
    claims = json.loads(push.b64u_dec(c))
    assert claims["aud"] == "https://web.push.apple.com" and claims["sub"].startswith("https://")
    raw = push.b64u_dec(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    v.key.public_key().verify(der, f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))  # wirft bei Fehler
    assert push.Vapid(tmp_path).public_b64 == v.public_b64  # Schlüssel bleibt gleich


def test_db_migration_adds_columns(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE clips (id INTEGER PRIMARY KEY, status TEXT)")
    con.commit()
    con.close()
    db = DB(path)
    cols = {r["name"] for r in db.all("PRAGMA table_info(clips)")}
    assert {"trim_start", "trim_end", "seen"} <= cols


def test_cleanup_rules(tmp_path):
    from app.watcher import Watcher
    s = Settings()
    s.data_dir = tmp_path / "d"
    s.__post_init__()
    s.clip_retention_days = 7
    db = DB(s.data_dir / "t.db")
    w = Watcher(db, s)
    old = time.time() - 10 * 86400

    def clip(status, created, posted=False):
        f = s.clips_dir / f"c{created}{status}{posted}.mp4"
        f.write_bytes(b"x")
        raw = s.tmp_dir / f"clip_{created}{status}{posted}_raw.mp4"
        raw.write_bytes(b"x")
        cid = db.insert("clips", {"status": status, "file": f.name, "raw_file": str(raw)})
        db.update("clips", cid, {"created_at": created})
        if posted:
            db.insert("posts", {"clip_id": cid, "platform": "tiktok", "posted_at": created})
        return cid, f, raw

    a = clip("ready", old)                 # alt, nicht gepostet -> weg
    b = clip("ready", old, posted=True)    # alt, gepostet -> bleibt, Rohdatei weg
    c = clip("ready", time.time())         # neu -> bleibt
    d = clip("failed", old)                # fehlgeschlagen -> weg
    removed = w.cleanup()
    ids = {r["id"] for r in db.all("SELECT id FROM clips")}
    assert ids == {b[0], c[0]} and removed["clips"] == 1 and removed["failed"] == 1
    assert not a[1].exists() and b[1].exists() and not b[2].exists() and c[2].exists()
    assert w.disk()["total_gb"] > 0


def test_trim_shortens_clip(tmp_path):
    from app.watcher import Watcher
    import app.clipper as clipper
    src = tmp_path / "vod.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=30",
                    "-f", "lavfi", "-i", "sine=duration=30", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(src)], check=True)
    s = Settings()
    s.data_dir = tmp_path / "d"
    s.__post_init__()
    db = DB(s.data_dir / "t.db")
    w = Watcher(db, s)
    ready = []

    async def cb(cid):
        ready.append(cid)
    w.on_clip_ready = cb

    async def fake_download(url, start, end, out, live_from_start=False):
        return src

    async def go():
        orig = clipper.download_section
        clipper.download_section = fake_download
        try:
            cid = db.insert("clips", {"status": "queued", "source_url": "x", "trim_start": 5, "trim_end": 10})
            await w.process(cid)
            await asyncio.sleep(0)
            first = db.get("clips", cid)
            db.update("clips", cid, {"trim_start": 0, "trim_end": 0})
            await w.process(cid)  # neu schneiden -> keine zweite Benachrichtigung
            await asyncio.sleep(0)
            return first, db.get("clips", cid)
        finally:
            clipper.download_section = orig
    first, second = asyncio.run(go())
    assert first["status"] == "ready" and 14 < first["duration"] < 16, first
    assert 29 < second["duration"] < 31
    assert len(ready) == 1


def test_push_endpoints_and_clip_filters(monkeypatch):
    from app import main
    calls = []

    async def fake_send(vapid, sub, data, subject):
        calls.append((sub["endpoint"], data))
        return 410 if "gone" in sub["endpoint"] else 201
    monkeypatch.setattr(push, "send", fake_send)
    with TestClient(main.app) as c:
        c.post("/api/login", json={"password": main.settings.app_password})
        assert len(c.get("/api/push").json()["key"]) > 80
        assert c.post("/api/push/subscribe", json={"endpoint": "http://x", "keys": {}}).status_code == 400
        for ep in ("https://fcm.googleapis.com/a", "https://web.push.apple.com/gone"):
            c.post("/api/push/subscribe", json={"endpoint": ep, "keys": {"p256dh": "a", "auth": "b"}})
        r = c.post("/api/push/test").json()
        assert r == {"sent": 1, "removed": 1}
        assert c.get("/api/push").json()["subscriptions"] == 1
        # Filter & Sortierung
        cid = main.db.insert("clips", {"status": "ready", "file": "x.mp4"})
        assert any(x["id"] == cid for x in c.get("/api/clips?status=new").json())
        c.patch(f"/api/clips/{cid}", json={"seen": 1})
        assert not any(x["id"] == cid for x in c.get("/api/clips?status=new").json())
        assert c.get("/api/clips?sort=best").status_code == 200
        assert c.get("/api/clips?status=posted").status_code == 200
        st = c.get("/api/settings").json()
        assert "disk" in st and st["clip_retention_days"] == 7
