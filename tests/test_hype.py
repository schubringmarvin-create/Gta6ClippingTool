from app.hype import HypeDetector, find_keywords
from app.chat import parse_line, parse_privmsg


def test_keywords():
    assert "kekw" in find_keywords("KEKW KEKW")
    assert find_keywords("CLIP IT") == ["clip it"]
    assert find_keywords("clipboard eclipse") == []


def test_parse_privmsg():
    line = "@badge-info=;color=#FF0000 :user!user@user.tmi.twitch.tv PRIVMSG #skilmasktimmy :OMG CLIP IT"
    assert parse_privmsg(line) == "OMG CLIP IT"
    assert parse_privmsg(":tmi.twitch.tv 001 justinfan :Welcome") is None
    tagged = "@color=#1E90FF;display-name=Boeshi_Fan;mod=0 :boeshi_fan!boeshi_fan@x.tmi.twitch.tv PRIVMSG #darealboeshi :KEKW"
    assert parse_line(tagged) == ("Boeshi_Fan", "KEKW", "#1E90FF")
    assert parse_line(":nick!nick@x PRIVMSG #c :hi") == ("nick", "hi", "")


def feed_rate(det, start, seconds, per_second, text="hi"):
    hit = None
    t = start
    step = 1 / per_second
    while t < start + seconds:
        h = det.feed(t, text)
        hit = hit or h
        t += step
    return hit, t


def test_quiet_chat_no_hype():
    det = HypeDetector()
    hit, _ = feed_rate(det, 0, 600, 0.5)
    assert hit is None


def test_burst_triggers_and_cooldown():
    det = HypeDetector(cooldown=120)
    hit, t = feed_rate(det, 0, 300, 0.3)
    assert hit is None
    hit, t = feed_rate(det, t, 15, 5, "KEKW CLIP IT")
    assert hit is not None
    assert hit.rate >= 1 and "clip it" in hit.keywords
    # direkt danach kein zweiter Treffer (Cooldown)
    hit2, _ = feed_rate(det, t, 20, 5, "KEKW")
    assert hit2 is None


def test_no_trigger_during_warmup():
    det = HypeDetector(warmup=60)
    hit, _ = feed_rate(det, 0, 30, 10, "KEKW")
    assert hit is None
