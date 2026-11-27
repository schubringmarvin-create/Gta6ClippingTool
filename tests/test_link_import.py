import asyncio

from fastapi.testclient import TestClient

from app import link_import as li

HTML = """<html><head><title>BOBBY VAN RIPER GTA VI Clipping | Content Rewards</title>
<meta property="og:description" content="Get paid to clip Bobby VanRiper's GTA VI content.">
</head><body><h1>BOBBY VAN RIPER GTA VI Clipping</h1>
<p>Get paid to clip Bobby VanRiper's GTA VI content. Twitch: <a href="https://www.twitch.tv/bobby_vanriper">twitch</a></p>
<div>$1.50 per 1,000 views</div><div>Max payout: $150 per clip</div>
<div>Minimum 1,500 views to get paid</div><div>Platforms: TikTok, Instagram Reels, YouTube Shorts</div>
<p>Clips must be 20-60 seconds. Use #gta6 #bobby in the caption.</p>
<script>var x = "twitch.tv/videos";</script></body></html>"""


def test_heuristics_reads_campaign_page():
    f = li.heuristics(li.extract(HTML))
    assert f["name"] == "BOBBY VAN RIPER GTA VI Clipping"
    assert f["twitch_channel"] == "bobby_vanriper"
    assert f["rate_per_1k"] == 1.5 and f["max_payout"] == 150 and f["min_views"] == 1500
    assert f["platforms"] == ["tiktok", "youtube", "instagram"]
    assert (f["min_seconds"], f["max_seconds"]) == (20, 60)
    assert f["required_tags"] == ["#bobby", "#gta6"]


def test_heuristics_french_and_cpm():
    f = li.heuristics(li.extract("<p>Clippe le contenu des streams de Bichou et gagne 1$ pour chaque 1000 vues.</p>"))
    assert f["rate_per_1k"] == 1
    f = li.heuristics(li.extract("<p>$2 CPM, pays nothing under 10k views. twitch.tv/BrettLiftsXP</p>"))
    assert f["rate_per_1k"] == 2 and f["min_views"] == 10000 and f["twitch_channel"] == "brettliftsxp"


def test_from_link_endpoint(monkeypatch):
    from app import main

    async def fake_fetch(url):
        return HTML
    monkeypatch.setattr(li, "fetch_page", fake_fetch)
    monkeypatch.setattr(main.settings, "anthropic_api_key", "")
    with TestClient(main.app) as c:
        c.post("/api/login", json={"password": main.settings.app_password})
        r = c.post("/api/campaigns/from-link", json={"url": "https://contentrewards.com/c/campaigns/x"}).json()
        assert r["method"] == "auto" and r["fields"]["twitch_channel"] == "bobby_vanriper"
        assert r["fields"]["url"].startswith("https://contentrewards.com")
        assert c.post("/api/campaigns/from-link", json={"url": "kein link"}).status_code == 400
        # Speichern mit Twitch-Kanal legt Streamer an
        body = {k: v for k, v in r["fields"].items()}
        async def no_poll():
            return None
        monkeypatch.setattr(main.watcher, "poll", no_poll)
        assert c.post("/api/campaigns", json=body).status_code == 200
        assert any(s["channel"] == "bobby_vanriper" and s["campaign_id"] for s in c.get("/api/streamers").json())
