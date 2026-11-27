import asyncio

from fastapi.testclient import TestClient

from app import campaign_search as cs


def test_parse_and_normalize():
    text = 'Gefunden:\n```json\n[{"name":"Timmy GTA","twitch_channel":"https://www.twitch.tv/SkilMaskTimmy",' \
           '"rate_per_1k":"1.5","required_tags":"#gta6, #timmy","platforms":"tiktok, youtube"}]\n```'
    items = [cs.normalize(x) for x in cs.parse_json_block(text)]
    assert items[0]["twitch_channel"] == "skilmasktimmy"
    assert items[0]["rate_per_1k"] == 1.5 and items[0]["required_tags"] == ["#gta6", "#timmy"]
    assert items[0]["platforms"] == ["tiktok", "youtube"] and items[0]["max_seconds"] == 60
    assert cs.parse_json_block("kein json") == []


class FakeTwitch:
    async def users(self, logins):
        return {l: {"id": "1"} for l in logins if l != "gibtsnicht"}

    async def live_streams(self, logins):
        return {"skilmasktimmy": {"game_name": "Grand Theft Auto VI", "viewer_count": 5000, "title": "RP"},
                "bobby": {"game_name": "Just Chatting", "viewer_count": 100, "title": "x"}}

    async def game_id(self, name):
        return "123"

    async def streams_by_game(self, gid, first=100):
        return [{"user_login": "SkilMaskTimmy", "user_name": "SkilMaskTimmy", "viewer_count": 5000, "title": "RP"}]


def test_check_twitch_marks_live_gta6():
    res = [cs.normalize({"name": n, "twitch_channel": n}) for n in ("skilmasktimmy", "bobby", "gibtsnicht")]
    out = asyncio.run(cs.check_twitch(FakeTwitch(), res))
    by = {r["twitch_channel"]: r for r in out}
    assert by["skilmasktimmy"]["live_gta6"] and by["skilmasktimmy"]["viewers"] == 5000
    assert by["bobby"]["live"] and not by["bobby"]["live_gta6"]
    assert by["gibtsnicht"]["twitch_exists"] is False


def test_search_endpoint_and_adopt(monkeypatch):
    from app import main

    async def fake_claude(api_key, known):
        return [{"name": "Timmy GTA", "streamer": "SkilMaskTimmy", "twitch_channel": "skilmasktimmy", "rate_per_1k": 2},
                {"name": "Ohne Twitch", "twitch_channel": ""}]

    monkeypatch.setattr(cs, "claude_search", fake_claude)
    monkeypatch.setattr(main.settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(main.watcher, "twitch", FakeTwitch())

    async def no_poll():
        return None
    monkeypatch.setattr(main.watcher, "poll", no_poll)
    with TestClient(main.app) as c:
        c.post("/api/login", json={"password": main.settings.app_password})
        assert c.post("/api/campaign-search").status_code == 200
        for _ in range(50):
            r = c.get("/api/campaign-search").json()
            if r["status"] != "running":
                break
            asyncio.run(asyncio.sleep(0.05))
        assert r["status"] == "done", r
        assert [x["twitch_channel"] for x in r["results"]] == ["skilmasktimmy"]  # ohne Twitch rausgefiltert
        assert r["results"][0]["live_gta6"] and r["live_gta6"][0]["channel"] == "skilmasktimmy"
        assert c.post("/api/campaign-search/adopt", json={"index": 0}).status_code == 200
        assert c.get("/api/campaign-search").json()["results"][0]["already_added"]
        st = [s for s in c.get("/api/streamers").json() if s["channel"] == "skilmasktimmy"][0]
        assert st["campaign_id"] and st["active"]
