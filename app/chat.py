"""Chat mitlesen. Twitch: anonym über IRC-WebSocket (kein Schlüssel nötig).
YouTube: über chat-downloader."""
from __future__ import annotations

import asyncio
import logging
import random
import threading
from typing import AsyncIterator

import websockets

log = logging.getLogger("chat")
TWITCH_IRC = "wss://irc-ws.chat.twitch.tv:443"


def parse_line(line: str) -> tuple[str, str, str] | None:
    """(Name, Text, Farbe) einer PRIVMSG-Zeile, sonst None."""
    text = parse_privmsg(line)
    if text is None:
        return None
    tags: dict[str, str] = {}
    rest = line
    if line.startswith("@"):
        raw, _, rest = line[1:].partition(" ")
        for part in raw.split(";"):
            k, _, v = part.partition("=")
            tags[k] = v
    nick = rest[1:].split("!", 1)[0] if rest.startswith(":") else ""
    return tags.get("display-name") or nick, text, tags.get("color", "")


def parse_privmsg(line: str) -> str | None:
    """Gibt den Nachrichtentext einer IRC-PRIVMSG-Zeile zurück."""
    if " PRIVMSG #" not in line:
        return None
    rest = line.split(" PRIVMSG #", 1)[1]
    if " :" not in rest:
        return None
    return rest.split(" :", 1)[1].strip()


async def twitch_chat(channel: str) -> AsyncIterator[tuple[str, str, str]]:
    """Liefert Chat-Nachrichten, verbindet bei Abbruch neu."""
    channel = channel.lower().lstrip("#")
    backoff = 2.0
    while True:
        try:
            async with websockets.connect(TWITCH_IRC, ping_interval=60) as ws:
                await ws.send("CAP REQ :twitch.tv/tags")
                await ws.send("PASS SCHMOOPIIE")
                await ws.send(f"NICK justinfan{random.randint(10000, 99999)}")
                await ws.send(f"JOIN #{channel}")
                backoff = 2.0
                async for raw in ws:
                    for line in str(raw).split("\r\n"):
                        if not line:
                            continue
                        if line.startswith("PING"):
                            await ws.send("PONG :tmi.twitch.tv")
                            continue
                        msg = parse_line(line)
                        if msg is not None:
                            yield msg
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # Netzwerkfehler: neu verbinden
            log.warning("Twitch-Chat %s getrennt: %s", channel, exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def youtube_chat(url: str) -> AsyncIterator[tuple[str, str, str]]:
    """YouTube-Livechat über chat-downloader (läuft in einem Thread)."""
    from chat_downloader import ChatDownloader  # optional

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, str, str] | None] = asyncio.Queue()
    stop = threading.Event()

    def run() -> None:
        try:
            for msg in ChatDownloader().get_chat(url):
                if stop.is_set():
                    break
                text = msg.get("message")
                if text:
                    author = (msg.get("author") or {}).get("name", "")
                    loop.call_soon_threadsafe(queue.put_nowait, (author, text, ""))
        except Exception as exc:
            log.warning("YouTube-Chat %s beendet: %s", url, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=run, daemon=True).start()
    try:
        while (item := await queue.get()) is not None:
            yield item
    finally:
        stop.set()
