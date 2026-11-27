"""Hook (erster Satz im Clip) und Caption-Vorschlag. Mit ANTHROPIC_API_KEY schreibt
Claude beides, sonst gibt es eine einfache Vorlage."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("hook")

PROMPT = """Du schreibst für einen kurzen GTA-6-Clip (TikTok/Shorts/Reels) von Streamer {streamer}.
Der Chat ist an dieser Stelle eskaliert (häufige Chat-Wörter: {keywords}).
Transkript des Clips:
---
{transcript}
---
Schreib genau zwei Zeilen, ohne Emojis in der ersten Zeile:
HOOK: ein erster Satz (max. 8 Wörter), bei dem man hängen bleibt. Nur belegte Inhalte aus dem Transkript, nichts erfinden.
CAPTION: eine kurze Post-Beschreibung (max. 150 Zeichen), Sprache: {language}.{tags}"""


def fallback(streamer: str, transcript: str, tags: list[str]) -> tuple[str, str]:
    hook = f"{streamer}: Der Chat rastet komplett aus" if streamer else "Der Chat rastet komplett aus"
    caption = f"{streamer} in GTA 6 😳 " + " ".join(tags) if streamer else "GTA 6 😳 " + " ".join(tags)
    return hook, caption.strip()


async def make_hook(api_key: str, streamer: str, transcript: str, keywords: list[str],
                    tags: list[str], mention: str = "", language: str = "Deutsch") -> tuple[str, str]:
    base_hook, base_caption = fallback(streamer, transcript, tags)
    if mention and mention.lower() not in base_caption.lower():
        base_caption = f"{base_caption} {mention}".strip()
    if not api_key:
        return base_hook, base_caption
    try:
        import anthropic
    except ImportError:
        return base_hook, base_caption

    tag_text = ""
    if tags or mention:
        tag_text = f" Die Caption MUSS enthalten: {' '.join(tags + ([mention] if mention else []))}"
    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        resp: Any = await client.beta.messages.create(
            model="claude-opus-5-5",
            max_tokens=1024,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": PROMPT.format(
                streamer=streamer or "unbekannt", keywords=", ".join(keywords) or "–",
                transcript=(transcript or "(kein Transkript)")[:4000], language=language, tags=tag_text)}],
        )
    except anthropic.APIError as exc:
        log.warning("Claude-Hook fehlgeschlagen: %s", exc)
        return base_hook, base_caption
    if resp.stop_reason == "refusal":
        return base_hook, base_caption
    text = "\n".join(b.text for b in resp.content if b.type == "text")
    hook, caption = base_hook, base_caption
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("HOOK:"):
            hook = line[5:].strip().strip('"') or hook
        elif line.upper().startswith("CAPTION:"):
            caption = line[8:].strip().strip('"') or caption
    # Pflicht-Tags sicherheitshalber ergänzen
    for t in tags + ([mention] if mention else []):
        if t and t.lower() not in caption.lower():
            caption += f" {t}"
    return hook, caption
