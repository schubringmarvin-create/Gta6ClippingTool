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
        r = c.post("/api/campaigns/import", json={"kampagnen": [{"name": "Boeshi", "rate": 0.5,
                                                                  "twitch_channel": "twitch.tv/DaRealBoeshi"}]}).json()
        assert r["imported"] == 1
        assert any(s["channel"] == "darealboeshi" and s["campaign_id"] for s in c.get("/api/streamers").json())
        assert len(c.get("/api/campaigns").json()) == 2
        assert c.get("/media/../clipper.db").status_code == 404
        assert c.get("/").status_code == 200


def test_settings_and_password_change():
    with TestClient(app) as c:
        c.post("/api/login", json={"password": "test-pw"})
        s = c.get("/api/settings").json()
        assert "twitch_ready" in s
        # Anthropic-Key speichern -> wird maskiert zurückgegeben
        r = c.put("/api/settings", json={"anthropic_api_key": "sk-ant-1234567890abcd"}).json()
        assert r["anthropic_api_key"].endswith("abcd") and "1234" not in r["anthropic_api_key"]
        # maskierter Wert zurückgeschickt = unverändert
        c.put("/api/settings", json={"anthropic_api_key": r["anthropic_api_key"]})
        from app.config import settings
        assert settings.anthropic_api_key == "sk-ant-1234567890abcd"
        assert c.put("/api/settings", json={"new_password": "kurz"}).status_code == 400
        assert c.put("/api/settings", json={"new_password": "neues-passwort"}).status_code == 200
        c.post("/api/logout")
        assert c.post("/api/login", json={"password": "test-pw"}).status_code == 401
        assert c.post("/api/login", json={"password": "neues-passwort"}).status_code == 200
        settings.app_password = "test-pw"
