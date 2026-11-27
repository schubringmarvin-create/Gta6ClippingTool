"""Hype-Erkennung: misst Chat-Nachrichten pro Sekunde gegen den Normalwert
und achtet auf typische Clip-Wörter (CLIP IT, KEKW, OMG ...)."""
from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field

KEYWORDS = [
    "clip it", "clip that", "clip", "kekw", "omegalul", "lul", "lmao", "lmfao", "omg",
    "pog", "poggers", "pogchamp", "wtf", "holy", "no way", "nooo", "w", "insane", "monkas",
    "💀", "😂", "🤣",
]
_word_res = [(k, re.compile(r"(?<!\w)" + re.escape(k) + r"(?!\w)", re.IGNORECASE)) for k in KEYWORDS]


def find_keywords(text: str) -> list[str]:
    hits = []
    for key, rx in _word_res:
        if rx.search(text):
            hits.append(key)
    # "clip it" zählt nicht zusätzlich als "clip"
    if "clip" in hits and ("clip it" in hits or "clip that" in hits):
        hits.remove("clip")
    return hits


@dataclass
class Hype:
    ts: float
    rate: float
    baseline: float
    score: float
    keywords: list[str]
    sample: list[str]


@dataclass
class HypeDetector:
    factor: float = 3.0          # Rate muss das x-fache des Normalwerts sein
    min_rate: float = 1.0        # mindestens so viele Nachrichten/s
    window: float = 10.0         # Messfenster in Sekunden
    baseline_tau: float = 300.0  # Normalwert glättet über ca. 5 Minuten
    warmup: float = 60.0         # erst nach so vielen Sekunden auslösen
    cooldown: float = 120.0      # Pause nach einem Treffer
    keyword_boost: float = 0.6   # Clip-Wörter senken die nötige Rate

    _msgs: deque = field(default_factory=deque)
    _baseline: float | None = None
    _last_update: float | None = None
    _started: float | None = None
    _last_hype: float = -math.inf

    def feed(self, ts: float, text: str) -> Hype | None:
        if self._started is None:
            self._started = ts
        self._msgs.append((ts, text, find_keywords(text)))
        return self.tick(ts)

    def _trim(self, now: float) -> None:
        while self._msgs and self._msgs[0][0] < now - self.window:
            self._msgs.popleft()

    def rate(self, now: float) -> float:
        self._trim(now)
        return len(self._msgs) / self.window

    @property
    def baseline(self) -> float:
        return self._baseline or 0.0

    def tick(self, now: float) -> Hype | None:
        if self._started is None:
            self._started = now
        rate = self.rate(now)
        # Normalwert als gleitender Mittelwert; während eines Hypes langsamer anpassen
        if self._baseline is None:
            self._baseline = rate
        elif self._last_update is not None:
            dt = max(0.0, now - self._last_update)
            tau = self.baseline_tau * (4 if rate > self.factor * self._baseline else 1)
            alpha = 1 - math.exp(-dt / tau)
            self._baseline += alpha * (rate - self._baseline)
        self._last_update = now

        if now - self._started < self.warmup or now - self._last_hype < self.cooldown:
            return None

        base = max(self._baseline, 0.05)
        kw_msgs = sum(1 for _, _, k in self._msgs if k)
        kw_ratio = kw_msgs / len(self._msgs) if self._msgs else 0.0
        needed = self.factor * base * (1 - self.keyword_boost * min(kw_ratio, 1.0))
        needed = max(needed, self.min_rate, base * 1.5)
        if rate < needed:
            return None

        self._last_hype = now
        counts: dict[str, int] = {}
        for _, _, kws in self._msgs:
            for k in kws:
                counts[k] = counts.get(k, 0) + 1
        top = sorted(counts, key=counts.get, reverse=True)[:5]  # type: ignore[arg-type]
        sample = [t for _, t, _ in list(self._msgs)[-8:]]
        score = round(rate / base + 2 * kw_ratio, 2)
        return Hype(ts=now, rate=round(rate, 2), baseline=round(base, 2), score=score, keywords=top, sample=sample)
