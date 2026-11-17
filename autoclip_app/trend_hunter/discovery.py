from __future__ import annotations

import json
import logging
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import requests

from .models import ChannelProfile

logger = logging.getLogger("autoclip.trend_hunter.discovery")

# Public Twitch web client ID (used by Twitch web application, Streamlink, and open-source tools)
TWITCH_PUBLIC_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"

# Built-in curated popular live streaming channels across YouTube and Twitch
SEED_TRENDING_CHANNELS: list[dict[str, str]] = [
    {"name": "IShowSpeed", "platform": "youtube", "handle": "IShowSpeed", "url": "https://www.youtube.com/@IShowSpeed"},
    {"name": "KaiCenat", "platform": "twitch", "handle": "kaicenat", "url": "https://www.twitch.tv/kaicenat"},
    {"name": "tarik", "platform": "twitch", "handle": "tarik", "url": "https://www.twitch.tv/tarik"},
    {"name": "xQc", "platform": "twitch", "handle": "xqc", "url": "https://www.twitch.tv/xqc"},
    {"name": "HasanAbi", "platform": "twitch", "handle": "hasanabi", "url": "https://www.twitch.tv/hasanabi"},
    {"name": "Caedrel", "platform": "twitch", "handle": "caedrel", "url": "https://www.twitch.tv/caedrel"},
    {"name": "Jynxzi", "platform": "twitch", "handle": "jynxzi", "url": "https://www.twitch.tv/jynxzi"},
    {"name": "Lofi Girl", "platform": "youtube", "handle": "LofiGirl", "url": "https://www.youtube.com/@LofiGirl"},
]


class ChannelDiscoveryService:
    """
    Autonomous channel discovery, live status verification, and metadata extraction
    for YouTube and Twitch live streams without mandatory paid APIs.
    """

    def __init__(self, request_timeout: float = 8.0) -> None:
        self.request_timeout = request_timeout
        self.http_session = requests.Session()
        self.http_session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        })

    def search_channels(self, query: str, platform: str | None = None) -> list[ChannelProfile]:
        """
        Discovers and checks live status for a search query (channel name, handle, or URL).
        """
        query = query.strip()
        if not query:
            return self.get_trending_channels()

        results: list[ChannelProfile] = []

        # 1. Check if the query is a direct URL
        if "twitch.tv/" in query:
            match = re.search(r"twitch\.tv/([a-zA-Z0-9_]+)", query)
            if match:
                channel = self.check_twitch_channel(match.group(1))
                return [channel]

        if "youtube.com/" in query or "youtu.be/" in query:
            channel = self.check_youtube_channel(query)
            return [channel]

        # 2. Query is a channel name or handle (e.g. 'IShowSpeed', 'kaicenat')
        clean_name = query.lstrip("@").strip()
        target_platform = platform.lower() if platform else "all"

        if target_platform in ("all", "youtube"):
            try:
                yt_profile = self.check_youtube_channel(clean_name)
                results.append(yt_profile)
            except Exception as exc:
                logger.debug("YouTube check failed for '%s': %s", clean_name, exc)

        if target_platform in ("all", "twitch"):
            try:
                tw_profile = self.check_twitch_channel(clean_name)
                results.append(tw_profile)
            except Exception as exc:
                logger.debug("Twitch check failed for '%s': %s", clean_name, exc)

        return results

    def check_twitch_channel(self, channel_name: str) -> ChannelProfile:
        """
        Queries Twitch public GraphQL API for live status, viewer count, category, and stream URL.
        Zero OAuth or API key required.
        """
        clean_name = channel_name.strip().lower().lstrip("@")
        channel_id = f"ch_twitch_{clean_name}"
        channel_url = f"https://www.twitch.tv/{clean_name}"

        profile = ChannelProfile(
            id=channel_id,
            channel_name=channel_name,
            platform="twitch",
            channel_url=channel_url,
            handle=clean_name,
            category="Just Chatting",
        )

        gql_query = """
        query GetStream($login: String!) {
            user(login: $login) {
                id
                login
                displayName
                profileImageURL(width: 150)
                stream {
                    id
                    title
                    viewersCount
                    game {
                        name
                    }
                }
            }
        }
        """

        try:
            resp = self.http_session.post(
                "https://gql.twitch.tv/gql",
                headers={
                    "Client-Id": TWITCH_PUBLIC_CLIENT_ID,
                    "Content-Type": "application/json",
                },
                json={"query": gql_query, "variables": {"login": clean_name}},
                timeout=self.request_timeout,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                user_data = data.get("user")
                if user_data:
                    profile.channel_name = user_data.get("displayName") or channel_name
                    profile.thumbnail_url = user_data.get("profileImageURL") or ""
                    stream = user_data.get("stream")
                    if stream:
                        profile.is_live = True
                        profile.current_viewers = int(stream.get("viewersCount") or 0)
                        profile.stream_title = str(stream.get("title") or "")
                        profile.stream_url = channel_url
                        game = stream.get("game")
                        if game and game.get("name"):
                            profile.category = game["name"]
        except Exception as exc:
            logger.warning("Twitch GQL lookup error for %s: %s", clean_name, exc)

        # Calculate initial demand score
        profile.demand_score = self._compute_initial_demand_score(profile)
        return profile

    def check_youtube_channel(self, channel_or_url: str) -> ChannelProfile:
        """
        Checks YouTube live stream status via public live URL and yt-dlp inspection.
        """
        clean = channel_or_url.strip()
        if clean.startswith("http://") or clean.startswith("https://"):
            if "/watch?v=" in clean:
                live_url = clean
                handle = clean.split("watch?v=")[-1]
            elif "/live" in clean:
                live_url = clean
                handle = clean.split("/")[-2].lstrip("@")
            else:
                handle = clean.rstrip("/").split("/")[-1].lstrip("@")
                live_url = f"https://www.youtube.com/@{handle}/live"
        else:
            handle = clean.lstrip("@")
            live_url = f"https://www.youtube.com/@{handle}/live"

        channel_id = f"ch_youtube_{handle.lower()}"
        channel_url = f"https://www.youtube.com/@{handle}"

        profile = ChannelProfile(
            id=channel_id,
            channel_name=handle,
            platform="youtube",
            channel_url=channel_url,
            handle=handle,
            category="Live Entertainment",
            stream_url=live_url,
        )

        # Inspect via yt-dlp without downloading
        try:
            import yt_dlp

            ydl_opts = {
                "skip_download": True,
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
                "playlist_items": "1",
                "socket_timeout": int(self.request_timeout),
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(live_url, download=False)
                if info:
                    profile.channel_name = info.get("uploader") or info.get("channel") or handle
                    profile.stream_title = info.get("title") or ""
                    profile.thumbnail_url = info.get("thumbnail") or ""
                    is_live = bool(info.get("is_live"))
                    live_status = str(info.get("live_status", "")).lower()
                    if is_live or live_status == "is_live":
                        profile.is_live = True
                        profile.current_viewers = int(info.get("concurrent_view_count") or 0)
                        profile.stream_url = info.get("webpage_url") or live_url
                    categories = info.get("categories", [])
                    if categories:
                        profile.category = categories[0]
        except Exception as exc:
            logger.debug("yt-dlp live check failed for %s: %s", live_url, exc)
            # Fallback to direct public HTML scrape check
            try:
                resp = self.http_session.get(live_url, timeout=self.request_timeout)
                if resp.status_code == 200:
                    html = resp.text
                    if '"isLive":true' in html or '"liveBroadcastDetails"' in html:
                        profile.is_live = True
                        title_m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
                        if title_m:
                            profile.stream_title = title_m.group(1)
                        viewers_m = re.search(r'"viewCount":\{"runs":\[\{"text":"([0-9,]+)"\}\]', html)
                        if viewers_m:
                            v_str = viewers_m.group(1).replace(",", "")
                            profile.current_viewers = int(v_str) if v_str.isdigit() else 0
            except Exception as scrape_exc:
                logger.debug("Public scrape fallback error for %s: %s", live_url, scrape_exc)

        profile.demand_score = self._compute_initial_demand_score(profile)
        return profile

    def get_trending_channels(self, refresh_live_status: bool = False) -> list[ChannelProfile]:
        """
        Returns the curated list of trending streaming channels.
        If refresh_live_status is True, queries current live state.
        """
        profiles: list[ChannelProfile] = []
        for seed in SEED_TRENDING_CHANNELS:
            prof = ChannelProfile(
                id=f"ch_{seed['platform']}_{seed['handle'].lower()}",
                channel_name=seed["name"],
                platform=seed["platform"],
                channel_url=seed["url"],
                handle=seed["handle"],
                stream_url=f"{seed['url']}/live" if seed["platform"] == "youtube" else seed["url"],
                category="Gaming & IRL",
            )
            if refresh_live_status:
                try:
                    if seed["platform"] == "twitch":
                        prof = self.check_twitch_channel(seed["handle"])
                    else:
                        prof = self.check_youtube_channel(seed["handle"])
                except Exception:
                    pass
            else:
                prof.demand_score = self._compute_initial_demand_score(prof)

            profiles.append(prof)

        # Sort with live channels first, then by demand score
        profiles.sort(key=lambda p: (1 if p.is_live else 0, p.demand_score, p.current_viewers), reverse=True)
        return profiles

    def _compute_initial_demand_score(self, profile: ChannelProfile) -> float:
        """
        Heuristic demand score (0-100) based on live status and available public viewer volume.
        """
        if not profile.is_live:
            # Base offline popularity baseline
            return 30.0

        v = max(0, profile.current_viewers)
        # Logarithmic scale: 100 viewers -> 50, 10,000 viewers -> 80, 50,000+ viewers -> 95+
        import math
        if v <= 0:
            return 55.0  # Live stream with hidden/unreported viewer count
        score = 40.0 + min(55.0, math.log10(v + 1) * 12.0)
        return round(min(99.0, max(45.0, score)), 1)
