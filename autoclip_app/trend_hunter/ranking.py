from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from .discovery import ChannelDiscoveryService
from .models import ChannelProfile
from .storage import TrendStorage

logger = logging.getLogger("autoclip.trend_hunter.ranking")


class ChannelRanker:
    """
    Intelligent Channel Ranking & Growth Detection Engine.
    Evaluates:
      - Raw public demand and viewer volume
      - Rapid growth velocity (derivative of concurrent viewership)
      - Engagement density (chat velocity normalized by audience size)
      - User custom priority weights (pins and high-priority creators)
    """

    def __init__(
        self,
        storage: TrendStorage,
        discovery: ChannelDiscoveryService | None = None,
        rapid_growth_threshold_pct_per_min: float = 10.0,
    ) -> None:
        self.storage = storage
        self.discovery = discovery or ChannelDiscoveryService()
        self.rapid_growth_threshold = rapid_growth_threshold_pct_per_min

    def rank_channels(self, channels: list[ChannelProfile] | None = None) -> list[ChannelProfile]:
        """
        Sorts channels by composite ranking score, placing active high-demand,
        rapidly growing, and high-priority channels at the top.
        """
        ch_list = channels if channels is not None else self.storage.list_channels()
        if not ch_list:
            return []

        scored_channels: list[tuple[float, ChannelProfile]] = []
        for ch in ch_list:
            score = self.compute_channel_ranking_score(ch)
            scored_channels.append((score, ch))

        # Sort descending by composite ranking score
        scored_channels.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored_channels]

    def compute_channel_ranking_score(self, channel: ChannelProfile) -> float:
        """
        Computes a 0-150 ranking index:
          - Base demand score (0-100)
          - Rapid growth bonus (+20 points if audience is surging)
          - Engagement density bonus (up to +15 points for high chat relative to viewers)
          - Custom priority tier multiplier (+15 points for High, +30 points for Critical Pin)
          - Offline penalty (-40 points)
        """
        if not channel.is_live:
            # Baseline offline score weighted down
            base = channel.demand_score * 0.4
            priority_bonus = (channel.custom_priority - 1) * 10.0
            return round(base + priority_bonus, 1)

        base_score = channel.demand_score

        # 1. Growth Detection
        growth_bonus = 0.0
        if channel.is_rapid_growing or channel.viewer_growth_rate >= self.rapid_growth_threshold:
            channel.is_rapid_growing = True
            growth_bonus = 20.0

        # 2. Engagement Density (e.g. 15 MPS for 500 viewers is exceptional engagement!)
        engagement_bonus = 0.0
        if channel.current_viewers > 0 and channel.engagement_density > 0:
            # 10+ MPS per 1k viewers is top 1% hype
            engagement_bonus = min(15.0, channel.engagement_density * 1.5)

        # 3. Custom Priority (1 = Normal, 2 = High, 3 = Pin)
        priority_bonus = (channel.custom_priority - 1) * 15.0

        composite = base_score + growth_bonus + engagement_bonus + priority_bonus
        return round(composite, 1)

    def detect_rapid_growth(self, channel: ChannelProfile) -> bool:
        """
        Checks rolling history for steep viewer acceleration.
        Returns True if audience grew faster than threshold.
        """
        history = channel.viewer_history
        if not history or len(history) < 2:
            return False

        oldest_t, oldest_v = history[0]
        latest_t, latest_v = history[-1]
        elapsed_min = max(0.2, (latest_t - oldest_t) / 60.0)

        if oldest_v <= 0:
            return False

        growth_pct_per_min = ((latest_v - oldest_v) / oldest_v) / elapsed_min * 100.0
        channel.viewer_growth_rate = round(growth_pct_per_min, 1)
        channel.is_rapid_growing = growth_pct_per_min >= self.rapid_growth_threshold
        return channel.is_rapid_growing

    async def refresh_channel_metrics(self, channel_id: str) -> ChannelProfile | None:
        """Queries live status for a specific channel and updates ranking metrics."""
        channel = self.storage.get_channel(channel_id)
        if not channel:
            return None

        try:
            if channel.platform == "twitch":
                handle = channel.handle or channel.channel_name
                fresh = self.discovery.check_twitch_channel(handle)
            else:
                handle = channel.handle or channel.channel_name
                fresh = self.discovery.check_youtube_channel(handle)

            # Preserve history & priority
            now_t = time.time()
            fresh.custom_priority = channel.custom_priority
            fresh.auto_monitor = channel.auto_monitor

            history = list(channel.viewer_history)
            if fresh.is_live and fresh.current_viewers > 0:
                history.append((now_t, fresh.current_viewers))
                # Keep last 10 samples
                if len(history) > 10:
                    history = history[-10:]
            fresh.viewer_history = history

            # Check growth
            self.detect_rapid_growth(fresh)
            self.storage.upsert_channel(fresh)
            return fresh

        except Exception as exc:
            logger.debug("Failed to refresh channel %s: %s", channel_id, exc)
            return channel


class PeriodicRankingScheduler:
    """
    Background worker that refreshes channel live metrics and rankings on an interval.
    """

    def __init__(self, ranker: ChannelRanker, refresh_interval_seconds: float = 90.0) -> None:
        self.ranker = ranker
        self.refresh_interval = refresh_interval_seconds
        self._is_running = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._is_running:
            return
        self._is_running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("PeriodicRankingScheduler started (interval: %.1fs)", self.refresh_interval)

    def stop(self) -> None:
        self._is_running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        while self._is_running:
            try:
                channels = self.ranker.storage.list_channels()
                for ch in channels:
                    if not self._is_running:
                        break
                    await self.ranker.refresh_channel_metrics(ch.id)
                    await asyncio.sleep(2.0)  # Gentle rate limit between checks
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Error in periodic ranking loop: %s", exc)

            await asyncio.sleep(self.refresh_interval)
