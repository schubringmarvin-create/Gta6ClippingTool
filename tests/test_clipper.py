import asyncio
import subprocess
from pathlib import Path

from app import clipper
from app.captions import build_ass, chunk_words


def test_chunk_words():
    words = [{"w": w, "s": i * 0.4, "e": i * 0.4 + 0.35} for i, w in enumerate("das ist ja komplett eskaliert".split())]
    chunks = chunk_words(words)
    assert [c["text"] for c in chunks] == ["das ist ja", "komplett eskaliert"]


def test_ass_contains_hook_and_offset():
    ass = build_ass([{"w": "hallo", "s": 12.0, "e": 12.5}], "Krasser {Hook}", offset=10)
    assert "Hook,,0,0,0,,Krasser (Hook)" in ass
    assert "0:00:02.00,0:00:02.50,Cap" in ass


def test_render_vertical(tmp_path: Path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=8",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=8", "-shortest", "-c:v", "libx264", "-c:a", "aac",
                    str(src)], check=True)
    out = tmp_path / "out.mp4"
    for layout in ("blur", "crop"):
        asyncio.run(clipper.render_vertical(src, out, start=2, duration=5, layout=layout,
                                            ass_text=build_ass([], "Test Hook")))
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=width,height", "-of", "csv=p=0", str(out)], capture_output=True, text=True)
        assert probe.stdout.strip() == "1080,1920"
        assert 4.5 < asyncio.run(clipper.probe_duration(out)) < 5.5
