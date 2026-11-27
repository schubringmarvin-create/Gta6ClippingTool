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
    hit, _ = feed_rate(det, 0, 900, 0.5)
    assert hit is None


def test_big_busy_chat_is_not_a_hype():
    """Großer Chat mit vielen Emotes (wie bei IShowSpeed) darf nicht ständig auslösen."""
    det = HypeDetector()
    hit, _ = feed_rate(det, 0, 1800, 20, "KEKW W LUL")
    assert hit is None
    assert 18 < det.baseline < 22


def test_real_burst_triggers_once_then_pause():
    det = HypeDetector(factor=4, cooldown=300)
    hit, t = feed_rate(det, 0, 600, 0.5)
    assert hit is None
    hit, t = feed_rate(det, t, 20, 6, "CLIP IT KEKW")   # 12x so schnell wie normal
    assert hit is not None and "clip it" in hit.keywords
    hit2, t = feed_rate(det, t, 60, 6, "KEKW")          # direkt danach: Pause
    assert hit2 is None


def test_short_spike_is_ignored():
    det = HypeDetector(factor=4, sustain=4)
    hit, t = feed_rate(det, 0, 600, 0.5)
    hit, t = feed_rate(det, t, 1.5, 30, "OMG")            # 1,5 s Ausreißer
    hit2, t = feed_rate(det, t, 30, 0.5)
    assert hit is None and hit2 is None


def test_three_times_is_not_enough_at_factor_4():
    det = HypeDetector(factor=4)
    hit, t = feed_rate(det, 0, 600, 1)
    hit, t = feed_rate(det, t, 30, 3, "KEKW")
    assert hit is None


def test_max_per_hour():
    det = HypeDetector(factor=4, cooldown=60, max_per_hour=2)
    hits = 0
    hit, t = feed_rate(det, 0, 600, 0.5)
    for _ in range(5):
        hit, t = feed_rate(det, t, 15, 8, "KEKW")
        hits += hit is not None
        hit, t = feed_rate(det, t, 120, 0.5)
    assert hits == 2


def test_no_trigger_during_warmup():
    det = HypeDetector(warmup=120)
    hit, _ = feed_rate(det, 0, 60, 10, "KEKW")
    assert hit is None
