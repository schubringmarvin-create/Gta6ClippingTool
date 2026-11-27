"""Regel-Check vor dem Posten und Einnahmen-Berechnung."""
from __future__ import annotations

from typing import Any


def check_clip(clip: dict[str, Any], campaign: dict[str, Any] | None, platform: str, caption: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(ok: bool, msg: str) -> None:
        checks.append({"ok": ok, "msg": msg})

    if campaign is None:
        add(False, "Keine Kampagne zugeordnet – ohne Erlaubnis wird nicht gepostet.")
        return {"allowed": False, "checks": checks}

    add(bool(campaign.get("clips_allowed")), "Streamer erlaubt Clips" if campaign.get("clips_allowed")
        else "Kampagne erlaubt KEINE Clips – Stopp.")

    if clip.get("status") != "ready":
        add(False, "Clip ist noch nicht fertig geschnitten.")

    platforms = [p.lower() for p in campaign.get("platforms") or []]
    if platforms:
        add(platform.lower() in platforms, f"Plattform {platform} erlaubt" if platform.lower() in platforms
            else f"Plattform {platform} ist in dieser Kampagne nicht erlaubt ({', '.join(platforms)}).")

    dur = float(clip.get("duration") or 0)
    lo, hi = int(campaign.get("min_seconds") or 0), int(campaign.get("max_seconds") or 0)
    if lo and dur < lo:
        add(False, f"Clip zu kurz: {dur:.0f}s (mindestens {lo}s).")
    elif hi and dur > hi:
        add(False, f"Clip zu lang: {dur:.0f}s (höchstens {hi}s).")
    else:
        add(True, f"Länge passt ({dur:.0f}s)")

    low = caption.lower()
    for tag in campaign.get("required_tags") or []:
        t = tag.strip()
        if t:
            add(t.lower() in low, f"Pflicht-Tag {t} vorhanden" if t.lower() in low else f"Pflicht-Tag fehlt: {t}")
    mention = (campaign.get("required_mention") or "").strip()
    if mention:
        add(mention.lower() in low, f"Pflicht-Erwähnung {mention} vorhanden" if mention.lower() in low
            else f"Pflicht-Erwähnung fehlt: {mention}")

    return {"allowed": all(c["ok"] for c in checks), "checks": checks}


def earnings(views: int, campaign: dict[str, Any] | None) -> float:
    """Einfache Schätzung: unter der Mindest-Aufrufzahl 0 €, sonst Rate pro 1.000,
    gedeckelt durch die maximale Auszahlung pro Clip."""
    if not campaign or views <= 0:
        return 0.0
    if views < int(campaign.get("min_views") or 0):
        return 0.0
    value = views / 1000 * float(campaign.get("rate_per_1k") or 0)
    cap = float(campaign.get("max_payout") or 0)
    if cap > 0:
        value = min(value, cap)
    return round(value, 2)


def campaign_from_json(item: dict[str, Any]) -> dict[str, Any]:
    """Liest Einträge aus einer kampagnen.json (z. B. vom Laptop) mit flexiblen Feldnamen."""
    def pick(*keys: str, default: Any = None) -> Any:
        for k in keys:
            if k in item and item[k] not in (None, ""):
                return item[k]
        return default

    def as_list(v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [s.strip() for s in v.replace(";", ",").split(",") if s.strip()]
        return [str(s) for s in v]

    allowed = pick("clips_allowed", "erlaubt", "allowed", "clips_erlaubt", default=True)
    if isinstance(allowed, str):
        allowed = allowed.strip().lower() in {"1", "ja", "yes", "true", "y"}
    return {
        "name": str(pick("name", "kampagne", "title", default="Kampagne")),
        "streamer": str(pick("streamer", "kanal", "channel", default="")),
        "platforms": [p.lower() for p in as_list(pick("platforms", "plattformen", default=[]))],
        "rate_per_1k": float(pick("rate_per_1k", "rate", "rate_pro_1000", "cpm", default=0) or 0),
        "min_views": int(pick("min_views", "mindest_aufrufe", "floor", default=0) or 0),
        "max_payout": float(pick("max_payout", "max_auszahlung", "cap", default=0) or 0),
        "min_seconds": int(pick("min_seconds", "min_sekunden", default=20) or 0),
        "max_seconds": int(pick("max_seconds", "max_sekunden", default=60) or 0),
        "required_tags": as_list(pick("required_tags", "tags", "pflicht_tags", "hashtags", default=[])),
        "required_mention": str(pick("required_mention", "erwaehnung", "mention", "caption_muss", default="")),
        "rules": str(pick("rules", "regeln", default="")) if not isinstance(pick("rules", "regeln"), list)
        else "\n".join(pick("rules", "regeln")),
        "clips_allowed": 1 if allowed else 0,
        "url": str(pick("url", "link", "regeln_url", default="")),
    }
