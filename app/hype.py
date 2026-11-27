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
    factor: float = 4.0          # Rate muss das x-fache des Durchschnitts sein
    min_rate: float = 1.0        # und mindestens so viele Nachrichten/s
    window: float = 10.0         # Messfenster in Sekunden
    baseline_tau: float = 600.0  # Durchschnitt über ca. 10 Minuten
    warmup: float = 120.0        # erst auslösen, wenn der Durchschnitt aussagekräftig ist
    cooldown: float = 300.0      # Pause nach einem Clip
    sustain: float = 4.0         # so lange muss die Rate über der Schwelle bleiben
    max_per_hour: int = 6        # höchstens so viele Clips pro Stunde

    _msgs: deque = field(default_factory=deque)
    _baseline: float | None = None
    _last_update: float | None = None
    _started: float | None = None
    _last_hype: float = -math.inf
    _above_since: float | None = None
    _hype_times: deque = field(default_factory=deque)
    _count: int = 0

    def feed(self, ts: float, text: str) -> Hype | None:
        if self._started is None:
            self._started = ts
        self._msgs.append((ts, text, find_keywords(text)))
        self._count += 1
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

    def threshold(self) -> float:
        """Ab dieser Rate (Nachrichten/s) gilt es als Hype."""
        return round(max(self.factor * max(self.baseline, 0.05), self.min_rate), 2)

    def tick(self, now: float) -> Hype | None:
        if self._started is None:
            self._started = now
        rate = self.rate(now)
        # Durchschnitt: gleitender Mittelwert. Während eines Hypes fast eingefroren,
        # damit der Durchschnitt nicht mit dem Hype mitwächst.
        elapsed = now - self._started
        if elapsed < self.baseline_tau:
            # Anfangs echter Durchschnitt seit Beginn (sonst wirkt ein normaler großer Chat wie ein Hype)
            self._baseline = self._count / max(elapsed, self.window)
        elif self._baseline is None:
            self._baseline = rate
        elif self._last_update is not None:
            dt = max(0.0, now - self._last_update)
            tau = self.baseline_tau * (10 if rate > self.factor * max(self._baseline, 0.05) else 1)
            self._baseline += (1 - math.exp(-dt / tau)) * (rate - self._baseline)
        self._last_update = now

        if rate < self.threshold():
            self._above_since = None
            return None
        if self._above_since is None:
            self._above_since = now
        if now - self._started < self.warmup or now - self._last_hype < self.cooldown:
            return None
        if now - self._above_since < self.sustain:
            return None
        # Der Chat muss auch in den letzten Sekunden noch voll dabei sein (kein abgeklungener Ausreißer)
        recent = sum(1 for ts, _, _ in self._msgs if ts >= now - 3) / 3
        if recent < self.threshold() * 0.7:
            return None
        while self._hype_times and self._hype_times[0] < now - 3600:
            self._hype_times.popleft()
        if len(self._hype_times) >= self.max_per_hour:
            return None

        self._last_hype = now
        self._above_since = None
        self._hype_times.append(now)
        base = max(self.baseline, 0.05)
        kw_msgs = sum(1 for _, _, k in self._msgs if k)
        kw_ratio = kw_msgs / len(self._msgs) if self._msgs else 0.0
        counts: dict[str, int] = {}
        for _, _, kws in self._msgs:
            for k in kws:
                counts[k] = counts.get(k, 0) + 1
        top = sorted(counts, key=counts.get, reverse=True)[:5]  # type: ignore[arg-type]
        sample = [t for _, t, _ in list(self._msgs)[-8:]]
        score = round(rate / base + 2 * kw_ratio, 2)
        return Hype(ts=now, rate=round(rate, 2), baseline=round(base, 2), score=score, keywords=top, sample=sample)

    def configure(self, factor: float, min_rate: float, cooldown: float, max_per_hour: int) -> None:
        self.factor, self.min_rate, self.cooldown, self.max_per_hour = factor, min_rate, cooldown, max_per_hour
