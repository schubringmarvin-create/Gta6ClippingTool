"""Stelle herunterladen (yt-dlp) und mit ffmpeg auf 9:16 schneiden."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

log = logging.getLogger("clipper")


class ClipError(RuntimeError):
    pass


async def run(cmd: list[str], cwd: Path | None = None, timeout: float = 900) -> str:
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(cwd) if cwd else None,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise ClipError(f"Zeitüberschreitung: {cmd[0]}")
    if proc.returncode != 0:
        tail = err.decode(errors="replace").strip().splitlines()[-6:]
        raise ClipError(f"{cmd[0]} fehlgeschlagen: " + " | ".join(tail))
    return out.decode(errors="replace")


async def download_section(url: str, start: float | None, end: float | None, out: Path,
                           live_from_start: bool = False) -> Path:
    """Lädt nur den Abschnitt start–end (Sekunden). Ohne Zeiten wird alles geladen (z. B. Twitch-Clip)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["yt-dlp", "--no-progress", "--no-playlist", "-f", "best[height<=1080]/best",
           "-o", str(out.with_suffix(".%(ext)s"))]
    if start is not None and end is not None:
        cmd += ["--download-sections", f"*{max(0, start):.1f}-{end:.1f}"]
    if live_from_start:
        cmd += ["--live-from-start"]
    cmd.append(url)
    await run(cmd, timeout=1200)
    found = sorted(out.parent.glob(out.stem + ".*"), key=lambda p: p.stat().st_mtime, reverse=True)
    found = [p for p in found if p.suffix not in {".part", ".ytdl"}]
    if not found:
        raise ClipError("Download lieferte keine Datei.")
    if found[0].stat().st_size < 50_000:
        found[0].unlink(missing_ok=True)
        raise ClipError("Download war leer – Stelle im VOD (noch) nicht verfügbar?")
    return found[0]


async def probe_duration(path: Path) -> float:
    out = await run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)])
    try:
        return float(json.loads(out)["format"]["duration"])
    except (KeyError, ValueError):
        return 0.0


def video_filter(layout: str, ass_name: str | None) -> str:
    if layout == "crop":
        chain = "[0:v]scale=-2:1920,crop=1080:1920,setsar=1[v0]"
    else:  # "blur": Original mittig, unscharfer Hintergrund – der Klassiker für Shorts
        chain = ("[0:v]split=2[a][b];"
                 "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:2[bg];"
                 "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v0]")
    if ass_name:
        chain += f";[v0]ass={ass_name}[v]"
    else:
        chain += ";[v0]null[v]"
    return chain


async def render_vertical(src: Path, out: Path, start: float = 0.0, duration: float | None = None,
                          layout: str = "blur", ass_text: str | None = None) -> Path:
    work = out.parent
    ass_name = None
    if ass_text:
        ass_name = out.stem + ".ass"
        (work / ass_name).write_text(ass_text, encoding="utf-8")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", str(src.resolve())]
    if duration:
        cmd += ["-t", f"{duration:.2f}"]
    cmd += ["-filter_complex", video_filter(layout, ass_name), "-map", "[v]", "-map", "0:a?",
            "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out.name]
    await run(cmd, cwd=work)
    if ass_name:
        (work / ass_name).unlink(missing_ok=True)
    return out


async def extract_audio(src: Path, out: Path, start: float = 0.0, duration: float | None = None) -> Path:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", str(src)]
    if duration:
        cmd += ["-t", f"{duration:.2f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", str(out)]
    await run(cmd)
    return out


async def thumbnail(src: Path, out: Path) -> Path:
    await run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "1", "-i", str(src),
               "-frames:v", "1", "-vf", "scale=360:-2", str(out)])
    return out


def tools_status() -> dict[str, bool]:
    return {name: shutil.which(name) is not None for name in ("ffmpeg", "ffprobe", "yt-dlp")}
