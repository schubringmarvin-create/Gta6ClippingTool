from fastapi.testclient import TestClient

from app.main import app


def test_login_and_crud():
    with TestClient(app) as c:
        assert c.get("/api/status").status_code == 401
        assert c.post("/api/login", json={"password": "falsch"}).status_code == 401
        assert c.post("/api/login", json={"password": "test-pw"}).status_code == 200
        st = c.get("/api/status").json()
        assert "streamers" in st
        k = c.post("/api/campaigns", json={"name": "Timmy", "rate_per_1k": 1, "required_tags": ["#gta6"]}).json()
        s = c.post("/api/streamers", json={"platform": "twitch", "channel": "https://twitch.tv/SkilMaskTimmy",
                                           "campaign_id": k["id"]}).json()
        assert s["channel"] == "skilmasktimmy"
        assert c.post("/api/streamers", json={"platform": "twitch", "channel": "skilmasktimmy"}).status_code == 409
        r = c.post("/api/campaigns/import", json={"kampagnen": [{"name": "Boeshi", "rate": 0.5}]}).json()
        assert r["imported"] == 1
        assert len(c.get("/api/campaigns").json()) == 2
        assert c.get("/media/../clipper.db").status_code == 404
        assert c.get("/").status_code == 200
