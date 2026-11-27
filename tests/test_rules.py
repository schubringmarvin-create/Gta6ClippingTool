from app.rules import campaign_from_json, check_clip, earnings

CAMP = {"clips_allowed": 1, "platforms": ["tiktok", "youtube"], "min_seconds": 20, "max_seconds": 60,
        "required_tags": ["#gta6"], "required_mention": "twitch.tv/skilmasktimmy", "rate_per_1k": 1.5,
        "min_views": 10000, "max_payout": 100}
CLIP = {"status": "ready", "duration": 45}


def test_check_passes():
    r = check_clip(CLIP, CAMP, "tiktok", "Krass #GTA6 twitch.tv/skilmasktimmy")
    assert r["allowed"], r


def test_check_missing_tag_platform_length():
    r = check_clip({"status": "ready", "duration": 75}, CAMP, "instagram", "nix")
    bad = [c["msg"] for c in r["checks"] if not c["ok"]]
    assert not r["allowed"] and len(bad) == 4


def test_no_campaign_or_not_allowed_stops():
    assert not check_clip(CLIP, None, "tiktok", "")["allowed"]
    assert not check_clip(CLIP, {**CAMP, "clips_allowed": 0}, "tiktok", "#gta6 twitch.tv/skilmasktimmy")["allowed"]


def test_earnings():
    assert earnings(5000, CAMP) == 0
    assert earnings(20000, CAMP) == 30.0
    assert earnings(10_000_000, CAMP) == 100


def test_import_flexible_json():
    c = campaign_from_json({"kampagne": "Boeshi", "streamer": "darealboeshi", "rate": "0.8",
                            "tags": "#gta6, #boeshi", "erlaubt": "ja", "regeln": ["Overlay nutzen", "Kein Reupload"]})
    assert c["name"] == "Boeshi" and c["rate_per_1k"] == 0.8 and c["required_tags"] == ["#gta6", "#boeshi"]
    assert c["clips_allowed"] == 1 and "Overlay" in c["rules"]
