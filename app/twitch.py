"""Twitch-Helix-API: Live-Status, aktuelles VOD und Community-Clips."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx

HELIX = "https://api.twitch.tv/helix"


def parse_time(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class Twitch:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None
        self._token_exp = 0.0
        self._http = httpx.AsyncClient(timeout=20)

    async def _auth(self) -> dict[str, str]:
        if not self._token or time.time() > self._token_exp - 300:
            r = await self._http.post("https://id.twitch.tv/oauth2/token", params={
                "client_id": self.client_id, "client_secret": self.client_secret, "grant_type": "client_credentials"})
            r.raise_for_status()
            data = r.json()
            self._token = data["access_token"]
            self._token_exp = time.time() + int(data.get("expires_in", 3600))
        return {"Client-Id": self.client_id, "Authorization": f"Bearer {self._token}"}

    async def _get(self, path: str, params: list[tuple[str, str]]) -> list[dict[str, Any]]:
        r = await self._http.get(f"{HELIX}/{path}", params=params, headers=await self._auth())
        if r.status_code == 401:
            self._token = None
            r = await self._http.get(f"{HELIX}/{path}", params=params, headers=await self._auth())
        r.raise_for_status()
        return r.json().get("data", [])

    async def live_streams(self, logins: list[str]) -> dict[str, dict[str, Any]]:
        """login -> Stream-Infos (nur wer gerade live ist)."""
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(logins), 100):
            chunk = logins[i:i + 100]
            for s in await self._get("streams", [("user_login", l) for l in chunk]):
                out[s["user_login"].lower()] = s
        return out

    async def latest_archive(self, user_id: str) -> dict[str, Any] | None:
        videos = await self._get("videos", [("user_id", user_id), ("type", "archive"), ("first", "1")])
        return videos[0] if videos else None

    async def clips_between(self, broadcaster_id: str, start: float, end: float) -> list[dict[str, Any]]:
        def iso(t: float) -> str:
            return datetime.utcfromtimestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ")
        clips = await self._get("clips", [("broadcaster_id", broadcaster_id), ("started_at", iso(start)),
                                          ("ended_at", iso(end)), ("first", "20")])
        return sorted(clips, key=lambda c: c.get("view_count", 0), reverse=True)

    async def users(self, logins: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(logins), 100):
            for u in await self._get("users", [("login", l) for l in logins[i:i + 100]]):
                out[u["login"].lower()] = u
        return out

    async def game_id(self, name: str) -> str | None:
        games = await self._get("games", [("name", name)])
        return games[0]["id"] if games else None

    async def streams_by_game(self, game_id: str, first: int = 100) -> list[dict[str, Any]]:
        return await self._get("streams", [("game_id", game_id), ("first", str(first))])
