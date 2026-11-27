"""Untertitel mit faster-whisper und ASS-Datei für ffmpeg (Untertitel + Hook oben)."""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger("captions")


def library_available() -> bool:
    """Ist faster-whisper installiert (für Untertitel per Knopfdruck)?"""
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def whisper_available() -> bool:
    """Automatische Untertitel für jeden Clip (nur mit ENABLE_CAPTIONS=1)."""
    from .config import settings
    return settings.enable_captions and library_available()


@lru_cache(maxsize=1)
def _model(name: str):
    from faster_whisper import WhisperModel
    return WhisperModel(name, device="cpu", compute_type="int8")


def transcribe(path: Path, model: str = "small", language: str = "", keep_loaded: bool = True
               ) -> tuple[str, list[dict[str, Any]]]:
    """Gibt (Text, Wörter mit Zeiten) zurück. Leer, wenn faster-whisper fehlt.
    keep_loaded=False gibt das Modell danach wieder frei (spart dauerhaft RAM)."""
    if not library_available():
        return "", []
    try:
        segments, _ = _model(model).transcribe(str(path), language=language or None, word_timestamps=True,
                                               vad_filter=True)
        words: list[dict[str, Any]] = []
        texts: list[str] = []
        for seg in segments:
            texts.append(seg.text.strip())
            for w in seg.words or []:
                if w.word.strip():
                    words.append({"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)})
        return " ".join(texts).strip(), words
    finally:
        if not keep_loaded:
            import gc
            _model.cache_clear()
            gc.collect()


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def chunk_words(words: list[dict[str, Any]], max_words: int = 3, max_gap: float = 0.7) -> list[dict[str, Any]]:
    """Fasst Wörter zu kurzen, knalligen Untertitel-Häppchen zusammen."""
    chunks: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []
    for w in words:
        if cur and (len(cur) >= max_words or w["s"] - cur[-1]["e"] > max_gap or cur[-1]["w"][-1:] in ".?!"):
            chunks.append({"text": " ".join(x["w"] for x in cur), "s": cur[0]["s"], "e": cur[-1]["e"]})
            cur = []
        cur.append(w)
    if cur:
        chunks.append({"text": " ".join(x["w"] for x in cur), "s": cur[0]["s"], "e": cur[-1]["e"]})
    # Lücken schließen, damit Untertitel nicht flackern
    for a, b in zip(chunks, chunks[1:]):
        if b["s"] - a["e"] < 0.3:
            a["e"] = b["s"]
    return chunks


def build_ass(words: list[dict[str, Any]], hook: str, offset: float = 0.0, hook_seconds: float = 3.5) -> str:
    """offset: Sekunden, um die der Clip gegenüber den Wort-Zeiten verschoben ist."""
    head = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,7,2,2,60,60,560,1
Style: Hook,DejaVu Sans,66,&H00000000,&H00000000,&H0000E5FF,&H0000E5FF,1,0,0,0,100,100,0,0,3,18,0,8,70,70,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if hook.strip():
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(hook_seconds)},Hook,,0,0,0,,{_esc(hook.strip())}")
    for c in chunk_words(words):
        s, e = c["s"] - offset, c["e"] - offset
        if e <= 0:
            continue
        lines.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},Cap,,0,0,0,,{_esc(c['text'].upper())}")
    return head + "\n".join(lines) + "\n"
