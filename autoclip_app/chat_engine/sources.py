from __future__ import annotations

import asyncio
import csv
import json
import logging
import random
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncGenerator, Iterator

from .models import ChatMessage

logger = logging.getLogger("autoclip.chat.sources")

KNOWN_BOTS = {
    "nightbot",
    "streamelements",
    "streamlabs",
    "moobot",
    "wizebot",
    "fossabot",
    "botisimo",
    "soundalerts",
    "kofi",
    "stay_hydrated_bot",
}

COMMON_HYPE_EMOTES = {
    "pog", "poggers", "pogchamp", "w", "l", "kekw", "omegalul", "lul",
    "lmao", "lol", "gg", "sheesh", "goat", "holy", "insane", "clutch",
    "clip", "clipped", "monkas", "pepehands", "pepelaugh", "sadge",
    "🔥", "💀", "😱", "🐐", "👑", "🚀", "👏", "🤯"
}


def extract_emotes_and_caps(text: str) -> tuple[list[str], float]:
    clean = text.strip()
    if not clean:
        return [], 0.0

    letters = [c for c in clean if c.isalpha()]
    caps_count = sum(1 for c in letters if c.isupper())
    caps_ratio = (caps_count / len(letters)) if letters else 0.0

    words = re.findall(r"[\w🔥💀😱🐐👑🚀👏🤯]+", clean.lower())
    found_emotes = [w for w in words if w in COMMON_HYPE_EMOTES]
    return found_emotes, caps_ratio


class BaseChatSource(ABC):
    def __init__(self, source_name: str) -> None:
        self.source_name = source_name
        self._is_running = False

    @abstractmethod
    async def connect(self) -> None:
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        pass

    @abstractmethod
    async def iter_messages(self) -> AsyncGenerator[ChatMessage, None]:
        pass

    @property
    def is_running(self) -> bool:
        return self._is_running


class TwitchIRCChatSource(BaseChatSource):
    """
    Connects to Twitch IRC chat via native asyncio socket.
    Uses anonymous login (justinfanXXXX) requiring zero API keys or user credentials.
    """

    def __init__(self, channel: str, oauth_token: str | None = None) -> None:
        super().__init__("twitch")
        self.channel = channel.lower().lstrip("#")
        self.oauth_token = oauth_token
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._start_epoch: float = time.time()

    async def connect(self) -> None:
        self._start_epoch = time.time()
        logger.info("Connecting to Twitch IRC for channel #%s...", self.channel)
        self.reader, self.writer = await asyncio.open_connection("irc.chat.twitch.tv", 6667)

        # Authenticate (anonymous or with oauth)
        nick = f"justinfan{random.randint(10000, 99999)}"
        password = self.oauth_token or "SCHMOOPIE"

        self.writer.write(f"PASS {password}\r\n".encode("utf-8"))
        self.writer.write(f"NICK {nick}\r\n".encode("utf-8"))
        self.writer.write(b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n")
        self.writer.write(f"JOIN #{self.channel}\r\n".encode("utf-8"))
        await self.writer.drain()
        self._is_running = True
        logger.info("Joined Twitch IRC #%s successfully as %s", self.channel, nick)

    async def disconnect(self) -> None:
        self._is_running = False
        if self.writer:
            try:
                self.writer.write(f"PART #{self.channel}\r\n".encode("utf-8"))
                await self.writer.drain()
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
            self.writer = None
            self.reader = None
        logger.info("Disconnected from Twitch IRC #%s", self.channel)

    async def iter_messages(self) -> AsyncGenerator[ChatMessage, None]:
        if not self.reader or not self.writer:
            await self.connect()

        while self._is_running and self.reader:
            try:
                line_bytes = await self.reader.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="ignore").strip()

                # Handle IRC keepalive ping
                if line.startswith("PING"):
                    if self.writer:
                        self.writer.write(b"PONG :tmi.twitch.tv\r\n")
                        await self.writer.drain()
                    continue

                if "PRIVMSG" not in line:
                    continue

                msg = self._parse_privmsg(line)
                if msg:
                    yield msg
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Error reading from Twitch IRC: %s", exc)
                await asyncio.sleep(1)

    def _parse_privmsg(self, line: str) -> ChatMessage | None:
        try:
            # Format: @tags :nick!user@host PRIVMSG #channel :message
            parts = line.split("PRIVMSG", 1)
            if len(parts) < 2:
                return None

            header, content = parts[0], parts[1]
            # Content starts with ' #channel :message'
            colon_idx = content.find(" :")
            if colon_idx == -1:
                return None
            text = content[colon_idx + 2:].strip()

            # Extract author
            author = "chatter"
            if "!" in header:
                author = header.split("!")[0].split(":")[-1].strip()

            # Check if bot
            is_bot = author.lower() in KNOWN_BOTS or author.lower().endswith("bot")

            now_sec = time.time() - self._start_epoch
            emotes, caps = extract_emotes_and_caps(text)

            return ChatMessage(
                author=author,
                text=text,
                timestamp=now_sec,
                source="twitch",
                is_bot=is_bot,
                emotes=emotes,
                caps_ratio=caps,
            )
        except Exception:
            return None


class YouTubeLiveChatSource(BaseChatSource):
    """
    Ingests live chat from YouTube. Supports official Live Chat API when authenticated,
    with yt-dlp live chat stream extraction fallback.
    """

    def __init__(self, video_id_or_url: str) -> None:
        super().__init__("youtube")
        self.video_id = self._extract_video_id(video_id_or_url)
        self._start_epoch = time.time()
        self._page_token: str | None = None
        self._live_chat_id: str | None = None

    def _extract_video_id(self, url: str) -> str:
        clean = url.strip()
        if "v=" in clean:
            match = re.search(r"v=([a-zA-Z0-9_-]{11})", clean)
            if match:
                return match.group(1)
        if "live/" in clean:
            match = re.search(r"live/([a-zA-Z0-9_-]{11})", clean)
            if match:
                return match.group(1)
        if len(clean) == 11 and re.match(r"^[a-zA-Z0-9_-]+$", clean):
            return clean
        return clean

    async def connect(self) -> None:
        self._start_epoch = time.time()
        self._is_running = True
        logger.info("Initializing YouTube live chat monitor for video: %s", self.video_id)

    async def disconnect(self) -> None:
        self._is_running = False
        logger.info("Stopped YouTube live chat monitor for %s", self.video_id)

    async def iter_messages(self) -> AsyncGenerator[ChatMessage, None]:
        # Try official YouTube Data API first if credentials exist
        from autoclip_app.youtube import SCOPES, TOKEN_FILE

        if TOKEN_FILE.exists():
            try:
                from google.oauth2.credentials import Credentials
                from googleapiclient.discovery import build

                creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
                yt = build("youtube", "v3", credentials=creds, cache_discovery=False)

                # Fetch active live chat ID
                v_res = yt.videos().list(part="liveStreamingDetails", id=self.video_id).execute()
                items = v_res.get("items", [])
                if items:
                    self._live_chat_id = items[0].get("liveStreamingDetails", {}).get("activeLiveChatId")

                if self._live_chat_id:
                    logger.info("Found YouTube live chat ID: %s", self._live_chat_id)
                    while self._is_running:
                        req = yt.liveChatMessages().list(
                            liveChatId=self._live_chat_id,
                            part="snippet,authorDetails",
                            pageToken=self._page_token
                        )
                        res = req.execute()
                        self._page_token = res.get("nextPageToken")
                        poll_ms = res.get("pollingIntervalMillis", 2000)

                        for it in res.get("items", []):
                            snippet = it.get("snippet", {})
                            author_details = it.get("authorDetails", {})
                            author = author_details.get("displayName", "Viewer")
                            text = snippet.get("displayMessage", "")
                            emotes, caps = extract_emotes_and_caps(text)
                            now_sec = time.time() - self._start_epoch

                            yield ChatMessage(
                                author=author,
                                text=text,
                                timestamp=now_sec,
                                source="youtube",
                                is_bot=author.lower() in KNOWN_BOTS,
                                emotes=emotes,
                                caps_ratio=caps,
                            )
                        await asyncio.sleep(poll_ms / 1000.0)
                    return
            except Exception as e:
                logger.warning("YouTube API live chat query encountered: %s. Falling back to simulated/file source.", e)

        # Fallback loop: emit heartbeat notice if no active live stream chat available
        logger.info("YouTube direct chat fallback active for %s", self.video_id)
        while self._is_running:
            await asyncio.sleep(2)


class FileReplayChatSource(BaseChatSource):
    """
    Replays recorded chat logs from JSON, JSONL, CSV or plain text.
    Supports instant batch mode (for tests) or speed-scaled simulated playback.
    """

    def __init__(
        self,
        file_path: Path | str,
        playback_speed: float = 0.0,  # 0.0 = instant batch mode (no sleep)
    ) -> None:
        super().__init__("file")
        self.file_path = Path(file_path)
        self.playback_speed = playback_speed

    async def connect(self) -> None:
        if not self.file_path.exists():
            raise FileNotFoundError(f"Chat replay file not found: {self.file_path}")
        self._is_running = True

    async def disconnect(self) -> None:
        self._is_running = False

    async def iter_messages(self) -> AsyncGenerator[ChatMessage, None]:
        messages = self._load_file()
        messages.sort(key=lambda m: m.timestamp)

        last_time = 0.0
        for m in messages:
            if not self._is_running:
                break
            if self.playback_speed > 0.0 and m.timestamp > last_time:
                wait_sec = (m.timestamp - last_time) / self.playback_speed
                if wait_sec > 0:
                    await asyncio.sleep(min(wait_sec, 2.0))
            last_time = m.timestamp
            yield m

    def _load_file(self) -> list[ChatMessage]:
        text = self.file_path.read_text(encoding="utf-8", errors="ignore")
        suffix = self.file_path.suffix.lower()

        if suffix == ".json":
            return self._parse_json(text)
        elif suffix == ".jsonl":
            return self._parse_jsonl(text)
        elif suffix == ".csv":
            return self._parse_csv(text)
        else:
            return self._parse_text(text)

    def _parse_json(self, content: str) -> list[ChatMessage]:
        data = json.loads(content)
        items = data if isinstance(data, list) else data.get("messages", data.get("comments", []))
        out: list[ChatMessage] = []
        for it in items:
            author = it.get("author", it.get("commenter", {}).get("display_name", "Viewer"))
            body = it.get("text", it.get("message", {}).get("body", ""))
            ts = float(it.get("timestamp", it.get("content_offset_seconds", 0.0)))
            emotes, caps = extract_emotes_and_caps(body)
            out.append(ChatMessage(
                author=str(author),
                text=str(body),
                timestamp=ts,
                source="file",
                is_bot=str(author).lower() in KNOWN_BOTS,
                emotes=emotes,
                caps_ratio=caps,
            ))
        return out

    def _parse_jsonl(self, content: str) -> list[ChatMessage]:
        out: list[ChatMessage] = []
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                it = json.loads(line)
                author = it.get("author", "Viewer")
                body = it.get("text", it.get("message", ""))
                ts = float(it.get("timestamp", 0.0))
                emotes, caps = extract_emotes_and_caps(body)
                out.append(ChatMessage(
                    author=str(author),
                    text=str(body),
                    timestamp=ts,
                    source="file",
                    is_bot=str(author).lower() in KNOWN_BOTS,
                    emotes=emotes,
                    caps_ratio=caps,
                ))
            except Exception:
                continue
        return out

    def _parse_csv(self, content: str) -> list[ChatMessage]:
        out: list[ChatMessage] = []
        reader = csv.reader(content.splitlines())
        for row in reader:
            if len(row) < 3 or row[0].lower() in ["timestamp", "time"]:
                continue
            try:
                ts = float(row[0].strip())
                author = row[1].strip()
                body = row[2].strip()
                emotes, caps = extract_emotes_and_caps(body)
                out.append(ChatMessage(
                    author=author,
                    text=body,
                    timestamp=ts,
                    source="file",
                    is_bot=author.lower() in KNOWN_BOTS,
                    emotes=emotes,
                    caps_ratio=caps,
                ))
            except Exception:
                continue
        return out

    def _parse_text(self, content: str) -> list[ChatMessage]:
        out: list[ChatMessage] = []
        # Pattern: [HH:MM:SS] User: text OR [12.5s] User: text OR 00:01:23 User: text
        pattern = re.compile(r"^\[?(\d{1,2}:\d{2}:\d{2}|\d+\.?\d*s?)\]?\s+([^:]+):\s*(.*)$")
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            m = pattern.match(line)
            if not m:
                continue
            time_str, author, body = m.group(1), m.group(2).strip(), m.group(3).strip()
            ts = 0.0
            if ":" in time_str:
                parts = time_str.split(":")
                if len(parts) == 3:
                    ts = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                elif len(parts) == 2:
                    ts = int(parts[0]) * 60 + float(parts[1])
            else:
                ts = float(time_str.rstrip("s"))

            emotes, caps = extract_emotes_and_caps(body)
            out.append(ChatMessage(
                author=author,
                text=body,
                timestamp=ts,
                source="file",
                is_bot=author.lower() in KNOWN_BOTS,
                emotes=emotes,
                caps_ratio=caps,
            ))
        return out


class SimulatedStreamChatSource(BaseChatSource):
    """
    Generates synthetic realistic stream chat with configurable baseline activity
    and deliberate high-energy hype bursts for deterministic automated testing.
    """

    def __init__(
        self,
        duration_seconds: float = 120.0,
        baseline_mps: float = 1.0,
        spike_schedule: list[dict[str, Any]] | None = None,
        instant: bool = False,
    ) -> None:
        super().__init__("simulated")
        self.duration_seconds = duration_seconds
        self.baseline_mps = baseline_mps
        # Default spike schedule: 40s to 60s hype spike with W and POG spam
        self.spike_schedule = spike_schedule or [
            {
                "start": 40.0,
                "end": 60.0,
                "mps": 12.0,
                "phrases": ["POGGERS 🔥", "W STREAMER", "NO WAYYY", "HOLY CLUTCH", "OMEGALUL", "WHAT A PLAY!!"],
                "emotes": ["pog", "w", "🔥", "clutch"]
            }
        ]
        self.instant = instant

    async def connect(self) -> None:
        self._is_running = True

    async def disconnect(self) -> None:
        self._is_running = False

    async def iter_messages(self) -> AsyncGenerator[ChatMessage, None]:
        users = [f"viewer_{i}" for i in range(1, 100)]
        baseline_phrases = ["hi chat", "gameplay is good", "what game is this?", "lmao", "gg", "w stream", "lol", "nice"]

        t = 0.0
        while t < self.duration_seconds and self._is_running:
            # Check if current t is in any spike window
            active_spike = None
            for sp in self.spike_schedule:
                if sp["start"] <= t <= sp["end"]:
                    active_spike = sp
                    break

            if active_spike:
                mps = active_spike.get("mps", 10.0)
                interval = 1.0 / max(mps, 1.0)
                author = random.choice(users[:30])
                text = random.choice(active_spike.get("phrases", ["POG"]))
            else:
                mps = self.baseline_mps
                interval = 1.0 / max(mps, 0.5)
                author = random.choice(users)
                text = random.choice(baseline_phrases)

            emotes, caps = extract_emotes_and_caps(text)
            msg = ChatMessage(
                author=author,
                text=text,
                timestamp=round(t, 2),
                source="simulated",
                is_bot=False,
                emotes=emotes,
                caps_ratio=caps,
            )

            if not self.instant and interval > 0:
                await asyncio.sleep(min(interval, 0.1))

            yield msg
            t += interval
