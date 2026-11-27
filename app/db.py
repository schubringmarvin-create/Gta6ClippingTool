"""SQLite-Speicher für Streamer, Kampagnen, Hype-Momente, Clips und Posts."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    streamer TEXT DEFAULT '',
    platforms TEXT DEFAULT '[]',
    rate_per_1k REAL DEFAULT 0,
    min_views INTEGER DEFAULT 0,
    max_payout REAL DEFAULT 0,
    min_seconds INTEGER DEFAULT 20,
    max_seconds INTEGER DEFAULT 60,
    required_tags TEXT DEFAULT '[]',
    required_mention TEXT DEFAULT '',
    rules TEXT DEFAULT '',
    clips_allowed INTEGER DEFAULT 1,
    url TEXT DEFAULT '',
    created_at REAL
);
CREATE TABLE IF NOT EXISTS streamers (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT 'twitch',
    channel TEXT NOT NULL,
    display_name TEXT DEFAULT '',
    active INTEGER DEFAULT 1,
    campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    created_at REAL,
    UNIQUE(platform, channel)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    streamer_id INTEGER REFERENCES streamers(id) ON DELETE CASCADE,
    ts REAL,
    rate REAL,
    baseline REAL,
    score REAL,
    keywords TEXT DEFAULT '[]',
    sample TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY,
    streamer_id INTEGER REFERENCES streamers(id) ON DELETE SET NULL,
    event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
    campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    status TEXT DEFAULT 'queued',
    source_url TEXT DEFAULT '',
    start_sec REAL,
    end_sec REAL,
    file TEXT DEFAULT '',
    raw_file TEXT DEFAULT '',
    thumb TEXT DEFAULT '',
    duration REAL DEFAULT 0,
    transcript TEXT DEFAULT '',
    words TEXT DEFAULT '[]',
    hook TEXT DEFAULT '',
    caption TEXT DEFAULT '',
    layout TEXT DEFAULT 'blur',
    error TEXT DEFAULT '',
    created_at REAL
);
CREATE TABLE IF NOT EXISTS live_sessions (
    id INTEGER PRIMARY KEY,
    streamer_id INTEGER REFERENCES streamers(id) ON DELETE CASCADE,
    started_at REAL,
    ended_at REAL,
    source TEXT DEFAULT 'live',
    UNIQUE(streamer_id, started_at)
);
CREATE TABLE IF NOT EXISTS push_subs (
    id INTEGER PRIMARY KEY,
    endpoint TEXT UNIQUE,
    p256dh TEXT,
    auth TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    clip_id INTEGER REFERENCES clips(id) ON DELETE CASCADE,
    platform TEXT,
    url TEXT DEFAULT '',
    caption TEXT DEFAULT '',
    views INTEGER DEFAULT 0,
    paid REAL,
    posted_at REAL
);
"""

JSON_FIELDS = {"platforms", "required_tags", "keywords", "sample", "words"}


class DB:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Neue Spalten in bestehenden Datenbanken nachrüsten."""
        wanted = {"clips": {"trim_start": "REAL DEFAULT 0", "trim_end": "REAL DEFAULT 0", "seen": "INTEGER DEFAULT 0",
                            "captions": "INTEGER DEFAULT 0"}}
        for table, cols in wanted.items():
            have = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols.items():
                if col not in have:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        d = dict(row)
        for k in JSON_FIELDS & d.keys():
            try:
                d[k] = json.loads(d[k] or "[]")
            except (TypeError, ValueError):
                d[k] = []
        return d

    @staticmethod
    def _enc(values: dict[str, Any]) -> dict[str, Any]:
        return {k: (json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS else v) for k, v in values.items()}

    def all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [self._row(r) for r in self._conn.execute(sql, args).fetchall()]  # type: ignore[misc]

    def one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute(sql, args).fetchone())

    def insert(self, table: str, values: dict[str, Any]) -> int:
        values = self._enc({"created_at": time.time(), **values} if table in {"campaigns", "streamers", "clips"} else values)
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        with self._lock:
            cur = self._conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))
            self._conn.commit()
            return int(cur.lastrowid)

    def update(self, table: str, row_id: int, values: dict[str, Any]) -> None:
        if not values:
            return
        values = self._enc(values)
        sets = ", ".join(f"{k} = ?" for k in values)
        with self._lock:
            self._conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*values.values(), row_id))
            self._conn.commit()

    def delete(self, table: str, row_id: int) -> None:
        with self._lock:
            self._conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
            self._conn.commit()

    def get_settings(self) -> dict[str, str]:
        with self._lock:
            return {r["key"]: r["value"] for r in self._conn.execute("SELECT key, value FROM settings")}

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                               "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
            self._conn.commit()

    def execute(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, args)
            self._conn.commit()

    def delete_where(self, table: str, column: str, value: Any) -> None:
        with self._lock:
            self._conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (value,))
            self._conn.commit()

    def get(self, table: str, row_id: int) -> dict[str, Any] | None:
        return self.one(f"SELECT * FROM {table} WHERE id = ?", (row_id,))
