from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ..youtube import upload_public_video, youtube_status
from .discovery import ChannelDiscoveryService
from .learning import FeedbackLearner
from .models import AutomationMode, ChannelProfile, LiveSessionState, TrendHunterConfig, ViralClipRecord
from .queue_worker import PersistentJobQueue, ResourceGuard
from .ranking import ChannelRanker, PeriodicRankingScheduler
from .storage import TrendStorage
from .stream_monitor import LiveStreamMonitorSession

logger = logging.getLogger("autoclip.trend_hunter.service")

ROOT = Path(__file__).resolve().parents[2]


class TrendHunterService:
    """
    Central orchestration service for Phase 2 Autonomous Viral Intelligence:
      - Smart Channel Ranking & Rapid Growth Detection
      - Multi-Stream Live Monitoring with Velocity Acceleration Prediction
      - Feedback Learning Loop from Human Actions & YouTube Analytics
      - Configurable Automation Levels (Manual, Semi-Auto, Full-Auto)
      - Resilient SQLite Job Queue with Resource Guard
      - Safe YouTube Shorts Publishing Integration
    """

    def __init__(self, storage: TrendStorage | None = None) -> None:
        self.storage = storage or TrendStorage()
        self.discovery = ChannelDiscoveryService()
        self.feedback_learner = FeedbackLearner(self.storage.db_path)
        self.ranker = ChannelRanker(storage=self.storage, discovery=self.discovery)
        self.job_queue = PersistentJobQueue(db_path=self.storage.db_path, resource_guard=ResourceGuard())
        self.job_queue.register_handler("EXTRACT_CLIP", self._handle_extract_clip_job)
        self.ranking_scheduler = PeriodicRankingScheduler(self.ranker, refresh_interval_seconds=90.0)

        self.active_monitors: dict[str, LiveStreamMonitorSession] = {}
        self.default_config = TrendHunterConfig()
        self._is_running = False

    async def _handle_extract_clip_job(self, job: JobQueueItem) -> None:
        """Processes an extraction job dispatched by the persistent SQLite job queue."""
        payload = job.payload or {}
        session_id = job.session_id
        monitor = self.active_monitors.get(session_id)
        if not monitor:
            logger.warning("Job %s: session %s no longer in active monitors", job.job_id, session_id)
            return

        spike_data = payload.get("spike", {})
        pred_data = payload.get("prediction", {})
        from ..chat_engine.models import SpikeEvent
        from .models import PredictionResult

        spike = SpikeEvent(
            peak_time=float(spike_data.get("peak_time", 0.0)),
            peak_mps=float(spike_data.get("peak_mps", 0.0)),
            baseline_mps=float(spike_data.get("baseline_mps", 1.0)),
            z_score=float(spike_data.get("z_score", 2.0)),
            clip_start=float(spike_data.get("clip_start", 0.0)),
            clip_end=float(spike_data.get("clip_end", 30.0)),
            dominant_keywords=spike_data.get("dominant_keywords", []),
        )
        prediction = PredictionResult(
            confidence_score=float(pred_data.get("confidence_score", 75.0)),
            rationale=str(pred_data.get("rationale", "")),
            signals=pred_data.get("signals", {}),
            is_early_prediction=bool(pred_data.get("is_early_prediction", False)),
            acceleration=float(pred_data.get("acceleration", 0.0)),
        )

        await monitor._extract_viral_clip(spike, prediction)

    def initialize_default_channels(self) -> None:
        """Seeds default curated trending streamers into the database if empty."""
        existing = self.storage.list_channels()
        if not existing:
            trending = self.discovery.get_trending_channels(refresh_live_status=False)
            for ch in trending:
                self.storage.upsert_channel(ch)
        # Start background services
        self.job_queue.start_worker()
        self.ranking_scheduler.start()

    def search_channels(self, query: str, platform: str | None = None) -> list[dict[str, Any]]:
        """Searches for channels and immediately updates their profile."""
        profiles = self.discovery.search_channels(query, platform=platform)
        for p in profiles:
            self.storage.upsert_channel(p)
        return [p.to_dict() for p in profiles]

    def list_tracked_channels(self) -> list[dict[str, Any]]:
        """Returns all tracked channels sorted by composite intelligent ranking."""
        channels = self.ranker.rank_channels()
        return [c.to_dict() for c in channels]

    def set_channel_priority(self, channel_id: str, priority: int) -> bool:
        """Updates channel priority tier (1=Normal, 2=High, 3=Critical Pin)."""
        return self.storage.set_channel_priority(channel_id, priority)

    def set_automation_level(self, mode: AutomationMode) -> AutomationMode:
        """Configures workflow autonomy: MANUAL, SEMI_AUTO, FULL_AUTO."""
        self.default_config.automation_level = mode
        logger.info("Automation level set to: %s", mode)
        return mode

    def add_channel(self, query: str, platform: str | None = None) -> dict[str, Any]:
        """Discovers and adds a new channel to active tracking."""
        results = self.discovery.search_channels(query, platform=platform)
        if not results:
            handle = query.lstrip("@").strip()
            ch_plat = platform.lower() if platform else "youtube"
            prof = ChannelProfile(
                id=f"ch_{ch_plat}_{handle.lower()}",
                channel_name=handle,
                platform=ch_plat,
                channel_url=f"https://www.{ch_plat}.com/{'@' if ch_plat == 'youtube' else ''}{handle}",
                handle=handle,
            )
            self.storage.upsert_channel(prof)
            return prof.to_dict()

        primary = results[0]
        self.storage.upsert_channel(primary)
        return primary.to_dict()

    def remove_channel(self, channel_id: str) -> bool:
        """Removes a channel and stops monitoring if active."""
        if channel_id in self.active_monitors:
            asyncio.create_task(self.stop_monitoring(channel_id))
        return self.storage.delete_channel(channel_id)

    async def start_monitoring(
        self,
        channel_id: str,
        config: TrendHunterConfig | None = None,
        source_video_fallback: Path | None = None,
        is_simulation: bool = False,
    ) -> dict[str, Any]:
        """
        Starts live stream monitoring on a tracked channel.
        """
        channel = self.storage.get_channel(channel_id)
        if not channel:
            raise ValueError(f"Channel not found: {channel_id}")

        if channel_id in self.active_monitors and self.active_monitors[channel_id]._is_running:
            return self.active_monitors[channel_id].state.to_dict()

        cfg = config or self.default_config
        monitor = LiveStreamMonitorSession(
            channel=channel,
            storage=self.storage,
            config=cfg,
            source_video_fallback=source_video_fallback,
            is_simulation=is_simulation,
            feedback_learner=self.feedback_learner,
            job_queue=self.job_queue,
        )
        self.active_monitors[channel_id] = monitor
        state = await monitor.start()
        return state.to_dict()

    async def stop_monitoring(self, channel_id: str) -> dict[str, Any]:
        """Stops live stream monitoring for a channel."""
        monitor = self.active_monitors.pop(channel_id, None)
        if not monitor:
            return {"status": "NOT_MONITORING", "channel_id": channel_id}
        state = await monitor.stop()
        return state.to_dict()

    def get_global_status(self) -> dict[str, Any]:
        """Returns consolidated dashboard telemetry."""
        channels = self.ranker.rank_channels()
        active_sessions = [m.state.to_dict() for m in self.active_monitors.values() if m._is_running]
        recent_clips = [c.to_dict() for c in self.storage.list_clips(limit=10)]
        yt_stat = youtube_status()
        weights = self.feedback_learner.current_weights.to_dict()

        return {
            "monitored_streams_count": len(active_sessions),
            "total_tracked_channels": len(channels),
            "automation_level": self.default_config.automation_level,
            "active_sessions": active_sessions,
            "tracked_channels": [c.to_dict() for c in channels],
            "recent_clips": recent_clips,
            "youtube_status": yt_stat,
            "learning_weights": weights,
        }

    def list_review_queue(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Returns clips from the review queue."""
        clips = self.storage.list_clips(status=status, limit=limit)
        return [c.to_dict() for c in clips]

    def update_clip_metadata(self, clip_id: str, title: str | None = None, description: str | None = None) -> bool:
        return self.storage.update_clip_status(clip_id=clip_id, status="PENDING_REVIEW", title=title, description=description)

    def reject_clip(self, clip_id: str, reason: str = "") -> bool:
        """Rejects a clip and trains adaptive weights with negative feedback."""
        clip = self.storage.get_clip(clip_id)
        if clip:
            self.feedback_learner.record_feedback(
                clip_id=clip_id,
                user_action="REJECTED",
                features={"confidence_score": clip.confidence_score, "demand_score": clip.demand_score},
                notes=reason,
            )
        return self.storage.update_clip_status(clip_id=clip_id, status="REJECTED")

    def approve_and_publish_clip(self, clip_id: str, copyright_confirmed: bool = True) -> dict[str, Any]:
        """
        Safe publishing workflow:
        Enforces copyright rights confirmation before upload, records positive feedback.
        """
        if not copyright_confirmed:
            raise ValueError("You must confirm copyright permissions before publishing.")

        clip = self.storage.get_clip(clip_id)
        if not clip:
            raise ValueError(f"Clip not found: {clip_id}")

        if clip.status == "REJECTED":
            raise ValueError(f"Cannot publish rejected clip '{clip_id}'. Restore or re-process first.")

        clip_path = Path(clip.shorts_clip_path or clip.raw_clip_path)
        if not clip_path.exists():
            raise FileNotFoundError(f"Video file not found at {clip_path}")

        # Publish via YouTube integration
        upload_result = upload_public_video(
            video_path=clip_path,
            title=clip.title[:100],
            description=clip.description,
            tags=clip.tags or ["Shorts", "Trending", "Viral"],
            category_id="22",
            privacy_status="public",
        )

        video_id = upload_result.get("video_id") or upload_result.get("id") or "published"
        self.storage.update_clip_status(
            clip_id=clip_id,
            status="PUBLISHED",
            copyright_confirmed=True,
            youtube_video_id=video_id,
        )

        # Train feedback weights with positive publication signal
        self.feedback_learner.record_feedback(
            clip_id=clip_id,
            user_action="PUBLISHED",
            features={"confidence_score": clip.confidence_score, "demand_score": clip.demand_score},
        )

        return {
            "status": "PUBLISHED",
            "clip_id": clip_id,
            "youtube_video_id": video_id,
            "youtube_url": f"https://www.youtube.com/shorts/{video_id}" if video_id != "published" else None,
            "message": "Clip successfully approved and published to YouTube Shorts!",
        }

    def sync_performance_analytics(self) -> int:
        """Fetches views/likes for published clips and adjusts AI weights."""
        return self.feedback_learner.fetch_youtube_analytics_for_published_clips()


# Global singleton instance
trend_hunter_service = TrendHunterService()
