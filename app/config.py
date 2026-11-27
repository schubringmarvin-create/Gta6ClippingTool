"""Einstellungen aus Umgebungsvariablen (.env auf dem Server, nie im Git)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DATA_DIR", "./data")).resolve())
    app_password: str = field(default_factory=lambda: os.environ.get("APP_PASSWORD", ""))
    secret_key: str = field(default_factory=lambda: os.environ.get("SECRET_KEY", ""))

    twitch_client_id: str = field(default_factory=lambda: os.environ.get("TWITCH_CLIENT_ID", ""))
    twitch_client_secret: str = field(default_factory=lambda: os.environ.get("TWITCH_CLIENT_SECRET", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))

    # Untertitel (faster-whisper) brauchen ca. 1–2 GB RAM extra – standardmäßig aus
    enable_captions: bool = field(default_factory=lambda: os.environ.get("ENABLE_CAPTIONS", "0") == "1")
    whisper_model: str = field(default_factory=lambda: os.environ.get("WHISPER_MODEL", "small"))
    caption_language: str = field(default_factory=lambda: os.environ.get("CAPTION_LANGUAGE", ""))

    # Hype-Erkennung
    hype_factor: float = field(default_factory=lambda: _f("HYPE_FACTOR", 3.0))
    hype_min_rate: float = field(default_factory=lambda: _f("HYPE_MIN_RATE", 1.0))
    hype_cooldown: float = field(default_factory=lambda: _f("HYPE_COOLDOWN", 120))

    # Clip-Fenster: Sekunden vor / nach dem Hype-Moment
    clip_before: float = field(default_factory=lambda: _f("CLIP_BEFORE", 35))
    clip_after: float = field(default_factory=lambda: _f("CLIP_AFTER", 10))
    # Wartezeit, bis die Stelle im Twitch-VOD verfügbar ist
    vod_delay: float = field(default_factory=lambda: _f("VOD_DELAY", 90))
    poll_interval: float = field(default_factory=lambda: _f("POLL_INTERVAL", 60))

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "clips").mkdir(exist_ok=True)
        (self.data_dir / "tmp").mkdir(exist_ok=True)
        if not self.secret_key:
            key_file = self.data_dir / ".secret_key"
            if key_file.exists():
                self.secret_key = key_file.read_text().strip()
            else:
                self.secret_key = secrets.token_hex(32)
                key_file.write_text(self.secret_key)

    @property
    def twitch_ready(self) -> bool:
        return bool(self.twitch_client_id and self.twitch_client_secret)

    @property
    def clips_dir(self) -> Path:
        return self.data_dir / "clips"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"


settings = Settings()
