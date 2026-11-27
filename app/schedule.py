"""Streamer-Zeitplan: Wann ist ein Streamer meistens live? Aus beobachteten Streams und
den letzten Twitch-VODs (kostenlos über die Twitch-API)."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def parse_duration(text: str) -> float:
    """Twitch-Dauer wie '3h2m10s' in Sekunden."""
    total = 0.0
    for value, unit in re.findall(r"(\d+)([hms])", text or ""):
        total += int(value) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


def heatmap(sessions: list[dict[str, Any]], days: int = 28, now: float | None = None) -> list[list[float]]:
    """7×24 Werte 0..1: Anteil der Wochen, in denen der Streamer in dieser Stunde live war."""
    now = now or time.time()
    since = now - days * 86400
    weeks = max(1.0, days / 7)
    hits = [[set() for _ in range(24)] for _ in range(7)]
    for s in sessions:
        start = max(float(s["started_at"]), since)
        end = float(s["ended_at"] or now)
        if end <= start:
            continue
        t = datetime.fromtimestamp(start, TZ).replace(minute=0, second=0, microsecond=0)
        while t.timestamp() < end:
            if t.timestamp() + 3600 > start:
                week = t.isocalendar()[1]
                hits[t.weekday()][t.hour].add(week)
            t += timedelta(hours=1)
    return [[round(min(1.0, len(hits[d][h]) / weeks), 2) for h in range(24)] for d in range(7)]


def summary(heat: list[list[float]]) -> str:
    """Kurzer Text wie 'meist Mo, Mi, Fr · 18–23 Uhr'."""
    day_score = [sum(row) for row in heat]
    if max(day_score) == 0:
        return "noch keine Daten"
    best_days = [DAYS[d] for d in range(7) if day_score[d] >= max(day_score) * 0.5]
    hour_score = [sum(heat[d][h] for d in range(7)) for h in range(24)]
    top = max(hour_score)
    hours = [h for h in range(24) if hour_score[h] >= top * 0.5]
    # zusammenhängenden Block um die Spitzenstunde suchen (über Mitternacht hinweg)
    peak = hour_score.index(top)
    lo = hi = peak
    while (lo - 1) % 24 in hours and (lo - 1) % 24 != peak:
        lo = (lo - 1) % 24
    while (hi + 1) % 24 in hours and (hi + 1) % 24 != lo:
        hi = (hi + 1) % 24
    days_txt = "täglich" if len(best_days) == 7 else ", ".join(best_days)
    return f"meist {days_txt} · {lo}–{(hi + 1) % 24} Uhr"


def next_likely(heat: list[list[float]], now: float | None = None, threshold: float = 0.5) -> float | None:
    """Nächste Stunde (Zeitstempel), in der der Streamer wahrscheinlich live ist."""
    t = datetime.fromtimestamp(now or time.time(), TZ).replace(minute=0, second=0, microsecond=0)
    for i in range(1, 24 * 7 + 1):
        x = t + timedelta(hours=i)
        if heat[x.weekday()][x.hour] >= threshold:
            return x.timestamp()
    return None
