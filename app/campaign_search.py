"""Kampagnen-Suche: Claude durchsucht das Netz nach Clipping-Kampagnen für GTA-6-Streamer
auf Twitch, danach prüft die Twitch-API, wer davon gerade live GTA 6 spielt."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from .config import Settings
from .twitch import Twitch

log = logging.getLogger("campaign_search")
GTA6_NAMES = ("grand theft auto vi", "gta vi", "gta 6")

PROMPT = """Suche im Internet nach aktuell laufenden Clipping-Kampagnen (Stand: heute, {today}),
bei denen Streamer oder Firmen für Kurzclips (TikTok, YouTube Shorts, Instagram Reels) pro Aufrufe bezahlen
und bei denen der Streamer auf **Twitch** **GTA 6 (Grand Theft Auto VI)** streamt. Typische Quellen:
Clipping-Plattformen und Kampagnen-Seiten (z. B. Whop-Content-Rewards, Vyro, Clipping-Discords),
Ankündigungen der Streamer, Regel-Dokumente der Kampagnen.

Bedingungen:
- Nur Kampagnen, die Clips aus den Streams ausdrücklich erlauben bzw. bezahlen.
- Nur Streamer mit Twitch-Kanal, die GTA 6 streamen (oder deren Kampagne GTA-6-Clips abdeckt).
- Nichts erfinden: Felder, die du nicht belegen kannst, leer lassen bzw. 0 setzen.
- Bis zu 15 Kampagnen, die besten zuerst (höchste Rate pro 1.000 Aufrufe).
{known}
Antworte am Ende ausschließlich mit einem JSON-Block in ```json ... ``` dieser Form:
[{{"name": "...", "streamer": "Anzeigename", "twitch_channel": "login-name ohne URL",
  "platforms": ["tiktok","youtube","instagram"], "rate_per_1k": 1.0, "min_views": 0, "max_payout": 0,
  "min_seconds": 0, "max_seconds": 0, "required_tags": ["#..."], "required_mention": "",
  "rules": "kurze Zusammenfassung der Regeln", "url": "Link zur Kampagne/Regeln", "source": "Quelle"}}]"""


def parse_json_block(text: str) -> list[dict[str, Any]]:
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("["): text.rfind("]") + 1]
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def _clean_channel(value: str) -> str:
    v = (value or "").strip().rstrip("/")
    v = v.split("twitch.tv/")[-1] if "twitch.tv/" in v else v
    return re.sub(r"[^a-zA-Z0-9_]", "", v.lstrip("@")).lower()


def _is_gta6(game: str) -> bool:
    return any(n in (game or "").lower() for n in GTA6_NAMES)


async def claude_search(api_key: str, known: list[str]) -> list[dict[str, Any]]:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key)
    known_text = f"- Diese Kampagnen kenne ich schon, nur aufnehmen, wenn sich etwas geändert hat: {', '.join(known)}\n" if known else ""
    messages: list[dict[str, Any]] = [{"role": "user", "content": PROMPT.format(
        today=time.strftime("%d.%m.%Y"), known=known_text)}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 12}]
    resp: Any = None
    for _ in range(5):  # pause_turn bei langen Suchen fortsetzen
        resp = await client.beta.messages.create(
            model="claude-opus-5-5", max_tokens=16000, betas=["server-side-fallback-2026-07-01"],
            fallbacks="default", tools=tools, messages=messages)
        if resp.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": resp.content}]
    if resp is None or resp.stop_reason == "refusal":
        raise RuntimeError("Die Suche wurde abgelehnt.")
    text = "\n".join(b.text for b in resp.content if b.type == "text")
    return parse_json_block(text)


def normalize(item: dict[str, Any]) -> dict[str, Any]:
    def num(k: str, cast=float) -> Any:
        try:
            return cast(item.get(k) or 0)
        except (TypeError, ValueError):
            return cast(0)
    tags = item.get("required_tags") or []
    if isinstance(tags, str):
        tags = tags.replace(",", " ").split()
    plats = item.get("platforms") or []
    if isinstance(plats, str):
        plats = [p.strip() for p in plats.split(",")]
    return {
        "name": str(item.get("name") or item.get("streamer") or "Kampagne")[:120],
        "streamer": str(item.get("streamer") or "")[:80],
        "twitch_channel": _clean_channel(str(item.get("twitch_channel") or "")),
        "platforms": [str(p).lower().strip() for p in plats if str(p).strip()],
        "rate_per_1k": num("rate_per_1k"), "min_views": num("min_views", int), "max_payout": num("max_payout"),
        "min_seconds": num("min_seconds", int) or 20, "max_seconds": num("max_seconds", int) or 60,
        "required_tags": [str(t) for t in tags][:10], "required_mention": str(item.get("required_mention") or ""),
        "rules": str(item.get("rules") or "")[:2000], "url": str(item.get("url") or ""),
        "source": str(item.get("source") or ""),
    }


async def check_twitch(twitch: Twitch | None, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ergänzt: gibt es den Kanal, ist er live, läuft gerade GTA 6?"""
    for r in results:
        r.update({"twitch_exists": None, "live": False, "game": "", "live_gta6": False, "viewers": 0, "title": ""})
    if not twitch:
        return results
    logins = sorted({r["twitch_channel"] for r in results if r["twitch_channel"]})
    if not logins:
        return results
    users = await twitch.users(logins)
    streams = await twitch.live_streams(logins)
    for r in results:
        ch = r["twitch_channel"]
        if not ch:
            continue
        r["twitch_exists"] = ch in users
        s = streams.get(ch)
        if s:
            r.update({"live": True, "game": s.get("game_name", ""), "viewers": s.get("viewer_count", 0),
                      "title": s.get("title", ""), "live_gta6": _is_gta6(s.get("game_name", ""))})
    return results


async def live_gta6_streams(twitch: Twitch | None, limit: int = 30) -> list[dict[str, Any]]:
    if not twitch:
        return []
    gid = await twitch.game_id("Grand Theft Auto VI")
    if not gid:
        return []
    streams = await twitch.streams_by_game(gid, first=limit)
    return [{"channel": s["user_login"].lower(), "name": s.get("user_name", ""), "viewers": s.get("viewer_count", 0),
             "title": s.get("title", "")} for s in streams]


async def run_search(settings: Settings, twitch: Twitch | None, known: list[str]) -> dict[str, Any]:
    if not settings.anthropic_api_key:
        raise RuntimeError("Für die Kampagnen-Suche wird ein Anthropic-API-Key gebraucht (Streamer → Einstellungen).")
    found = [normalize(x) for x in await claude_search(settings.anthropic_api_key, known)]
    found = [f for f in found if f["twitch_channel"]]  # nur Twitch
    found = await check_twitch(twitch, found)
    live = await live_gta6_streams(twitch)
    live_set = {s["channel"] for s in live}
    for f in found:
        f["in_gta6_live_list"] = f["twitch_channel"] in live_set
    found.sort(key=lambda f: (not f["live_gta6"], -f["rate_per_1k"]))
    return {"results": found, "live_gta6": live, "twitch_checked": twitch is not None}
