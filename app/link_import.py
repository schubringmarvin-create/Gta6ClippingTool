"""Kampagne aus einem Link: Seite laden, Text/Metadaten herausziehen, Felder erkennen.
Mit Anthropic-API-Key liest Claude den Seiteninhalt, sonst helfen Muster (Regex)."""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
from typing import Any

import httpx

log = logging.getLogger("link_import")
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
TWITCH_SKIP = {"videos", "directory", "settings", "p", "downloads", "jobs", "turbo", "subs", "search", "login"}
FIELDS = ("name", "streamer", "twitch_channel", "platforms", "rate_per_1k", "min_views", "max_payout",
          "min_seconds", "max_seconds", "required_tags", "required_mention", "rules")


async def fetch_page(url: str) -> str:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": UA,
                                 "Accept-Language": "de,en;q=0.8"}) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text[:3_000_000]


def _meta(html: str, name: str) -> str:
    for pattern in (rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]*content=["\']([^"\']*)["\']',
                    rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']{re.escape(name)}["\']'):
        m = re.search(pattern, html, re.I)
        if m:
            return html_lib.unescape(m.group(1)).strip()
    return ""


def extract(html: str) -> dict[str, str]:
    """Titel, Beschreibung, sichtbarer Text und eingebettete JSON-Daten."""
    title = _meta(html, "og:title") or html_lib.unescape(
        (re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S) or [None, ""])[1]).strip()
    desc = _meta(html, "og:description") or _meta(html, "description")
    blobs = re.findall(r'<script[^>]+(?:application/ld\+json|__NEXT_DATA__|application/json)[^>]*>(.*?)</script>',
                       html, re.I | re.S)
    body = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    body = re.sub(r"<br\s*/?>|</(p|div|li|h\d|tr)>", "\n", body, flags=re.I)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", body))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    # Links behalten (Twitch-Kanal steht oft nur im href)
    links = " ".join(sorted(set(re.findall(r'href=["\'](https?://[^"\']+)', html, re.I))))
    return {"title": title, "description": desc, "text": text[:20000], "json": " ".join(blobs)[:20000],
            "links": links[:5000]}


def _num(s: str) -> float:
    s = s.strip().lower().replace(" ", "")
    mult = 1000 if s.endswith("k") else 1_000_000 if s.endswith("m") else 1
    s = s.rstrip("km")
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", s):  # 10,000 / 10.000
        s = re.sub(r"[.,]", "", s)
    else:
        s = s.replace(",", ".")
    try:
        return float(s) * mult
    except ValueError:
        return 0.0


def heuristics(page: dict[str, str]) -> dict[str, Any]:
    blob = "\n".join([page.get("title", ""), page.get("description", ""), page.get("text", ""),
                      page.get("json", ""), page.get("links", "")])
    low = blob.lower()
    out: dict[str, Any] = {}

    name = re.split(r"\s+[|–-]\s+(?:content rewards|heistclips|whop|vyro).*$", page.get("title", ""), flags=re.I)[0]
    if name:
        out["name"] = name.strip()[:120]

    channels = [c for c in re.findall(r"twitch\.tv/([A-Za-z0-9_]{3,25})", blob) if c.lower() not in TWITCH_SKIP]
    if channels:
        ch = max(set(c.lower() for c in channels), key=lambda c: sum(1 for x in channels if x.lower() == c))
        out["twitch_channel"] = ch
        out["streamer"] = ch
        out["required_mention"] = ""

    num = r"(\d+(?:[.,]\d+)?)"
    rate_patterns = [
        rf"\$\s?{num}\s*(?:usd)?\s*(?:/|per|pro|for every|for each|pour chaque|pour|für je|je)\s*(?:1[ .,]?000|1k|thousand|mille|k)\b",
        rf"{num}\s?\$\s*(?:/|per|pro|for every|for each|pour chaque|pour|für je|je)\s*(?:1[ .,]?000|1k|thousand|mille|k)\b",
        rf"\$\s?{num}\s*cpm", rf"{num}\s?\$\s*cpm", rf"cpm\D{{0,12}}\$?\s?{num}",
        rf"\$\s?{num}\s*/\s*1k",
    ]
    for pat in rate_patterns:
        m = re.search(pat, low)
        if m and 0 < _num(m.group(1)) <= 50:
            out["rate_per_1k"] = _num(m.group(1))
            break

    views = r"(\d+(?:[.,]\d+)?\s?[km]?)"
    for pat in (rf"(?:min(?:imum)?|at least|mindestens|ab|minimum de)\s*(?:of\s*)?{views}\s*(?:views|aufrufe|vues)",
                rf"(?:under|unter|below)\s*{views}\s*(?:views|aufrufe|vues)",
                rf"{views}\s*(?:views|aufrufe|vues)\s*(?:minimum|min\.?|to (?:qualify|get paid))"):
        m = re.search(pat, low)
        if m and _num(m.group(1)) >= 100:
            out["min_views"] = int(_num(m.group(1)))
            break

    m = re.search(rf"(?:max(?:imum)?(?:\s*payout)?|cap(?:ped)?(?:\s*at)?|maximal|plafond)\D{{0,25}}\$\s?{num}", low) or \
        re.search(rf"\$\s?{num}\s*(?:max|cap)\b", low)
    if m and _num(m.group(1)) > 0:
        out["max_payout"] = _num(m.group(1))

    plats = [p for p, keys in {"tiktok": ("tiktok",), "youtube": ("youtube shorts", "youtube", "shorts"),
                               "instagram": ("instagram", "reels")}.items() if any(k in low for k in keys)]
    if plats:
        out["platforms"] = plats

    m = re.search(r"(\d{1,3})\s*(?:-|–|bis|to)\s*(\d{1,3})\s*(?:s\b|sec|seconds|sekunden)", low)
    if m and int(m.group(1)) < int(m.group(2)) <= 180:
        out["min_seconds"], out["max_seconds"] = int(m.group(1)), int(m.group(2))

    tags = sorted(set(t for t in re.findall(r"(?<![\w#])#[A-Za-z][A-Za-z0-9_]{2,30}", page.get("text", "") +
                                                " " + page.get("description", ""))))[:8]
    if tags:
        out["required_tags"] = tags
    if page.get("description"):
        out["rules"] = page["description"][:1500]
    return out


CLAUDE_PROMPT = """Hier ist der Inhalt einer Seite einer Clipping-Kampagne (Link: {url}).
Lies daraus die Angaben heraus. Nichts erfinden – was nicht drinsteht, leer lassen bzw. 0.
Antworte nur mit einem JSON-Objekt in ```json ... ``` mit diesen Feldern:
{{"name": "", "streamer": "", "twitch_channel": "login ohne URL", "platforms": ["tiktok","youtube","instagram"],
 "rate_per_1k": 0, "min_views": 0, "max_payout": 0, "min_seconds": 0, "max_seconds": 0,
 "required_tags": ["#..."], "required_mention": "Pflicht-Text/Link in der Caption, falls verlangt",
 "rules": "kurze deutsche Zusammenfassung der wichtigsten Regeln"}}

TITEL: {title}
BESCHREIBUNG: {description}
LINKS: {links}
TEXT:
{text}
EINGEBETTETE DATEN:
{json}"""


async def claude_extract(api_key: str, url: str, page: dict[str, str]) -> dict[str, Any]:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key)
    resp: Any = await client.beta.messages.create(
        model="claude-opus-5-5", max_tokens=4096, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": CLAUDE_PROMPT.format(url=url, **{k: page.get(k, "")[:12000] for k in
                                                                              ("title", "description", "links", "text", "json")})}])
    if resp.stop_reason == "refusal":
        return {}
    text = "\n".join(b.text for b in resp.content if b.type == "text")
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def clean(fields: dict[str, Any], url: str) -> dict[str, Any]:
    from .campaign_search import normalize
    out = normalize({**fields, "url": url})
    out.pop("source", None)
    return out


async def campaign_from_link(url: str, api_key: str = "") -> dict[str, Any]:
    html = await fetch_page(url)
    page = extract(html)
    fields = heuristics(page)
    method = "auto"
    if api_key:
        try:
            ai = await claude_extract(api_key, url, page)
            if ai:
                fields.update({k: v for k, v in ai.items() if v not in (None, "", 0, [])})
                method = "claude"
        except Exception as exc:
            log.warning("Claude-Auslesen fehlgeschlagen: %s", exc)
    result = clean(fields, url)
    found = [k for k in FIELDS if fields.get(k) not in (None, "", 0, [])]
    thin = len(page["text"]) < 300 and not page["json"]
    return {"fields": result, "found": found, "method": method, "page_was_empty": thin}
