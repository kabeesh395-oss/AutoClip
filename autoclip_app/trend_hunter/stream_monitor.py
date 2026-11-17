from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..chat_engine.detector import ChatSpikeDetector
from ..chat_engine.models import ChatMessage, SpikeDetectorConfig, SpikeEvent
from ..chat_engine.sources import BaseChatSource, SimulatedStreamChatSource, TwitchIRCChatSource, YouTubeLiveChatSource
from .audio_monitor import AudioLoudnessMonitor
from .clip_pipeline import ViralClipPipeline
from .demand import DemandEngine
from .live_buffer import RollingLiveBuffer
from .models import ChannelProfile, LiveSessionState, TrendHunterConfig, ViralClipRecord
from .storage import TrendStorage

logger = logging.getLogger("autoclip.trend_hunter.monitor")

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS_DIR = ROOT / "data" / "outputs" / "viral_clips"


class LiveStreamMonitorSession:
    """
    Autonomous live stream monitor for a single channel.
    Synchronizes:
      - Rolling 5-minute video ring buffer (FFmpeg/Streamlink)
      - Real-time chat message ingestion (Twitch IRC / YouTube Live Chat)
      - Statistical spike detection & Shannon entropy spam filtering
      - Audio loudness & shouting surge analysis
      - Multi-signal viral moment fusion (Chat + Audio + Viewers + Transcript)
      - Pre-roll (20s) and Post-roll (40s) preservation
      - Autonomous 9:16 Shorts rendering with Whisper & kinetic ASS subtitles
    """

    def __init__(
        self,
        channel: ChannelProfile,
        storage: TrendStorage,
        config: TrendHunterConfig | None = None,
        source_video_fallback: Path | None = None,
        is_simulation: bool = False,
    ) -> None:
        self.channel = channel
        self.storage = storage
        self.config = config or TrendHunterConfig()
        self.source_video_fallback = source_video_fallback
        self.is_simulation = is_simulation

        self.session_id = f"sess_{uuid.uuid4().hex[:8]}"
        self.state = LiveSessionState(
            session_id=self.session_id,
            channel_id=channel.id,
            channel_name=channel.channel_name,
            platform=channel.platform,
            stream_url=channel.stream_url or channel.channel_url,
            current_viewers=channel.current_viewers,
            current_demand_score=channel.demand_score,
        )

        # Components
        self.live_buffer = RollingLiveBuffer(
            session_id=self.session_id,
            retention_seconds=self.config.buffer_retention_seconds,
            segment_seconds=5.0,
        )
        self.audio_monitor = AudioLoudnessMonitor()
        self.demand_engine = DemandEngine()
        self.clip_pipeline = ViralClipPipeline(output_dir=OUTPUTS_DIR)

        # Chat detector configuration aligned with sensitivity
        spike_cfg = SpikeDetectorConfig(
            sensitivity=self.config.sensitivity,
            pre_roll_seconds=self.config.pre_roll_seconds,
            post_roll_seconds=self.config.post_roll_seconds,
            min_confidence=self.config.min_confidence,
        )
        self.spike_detector = ChatSpikeDetector(
            config=spike_cfg,
            session_id=self.session_id,
        )

        self._chat_source: BaseChatSource | None = None
        self._monitor_tasks: list[asyncio.Task] = []
        self._is_running = False
        self._last_clip_peak_time: float = -999.0
        self._recent_spikes: list[float] = []

    async def start(self) -> LiveSessionState:
        """Starts live buffer capture, chat stream, and analytics loops."""
        self._is_running = True
        self.state.status = "MONITORING"
        self.storage.create_session(self.state, buffer_dir=str(self.live_buffer.buffer_dir))

        # 1. Start live buffer recording
        stream_url = self.channel.stream_url or self.channel.channel_url
        await self.live_buffer.start_capture(
            stream_url=stream_url,
            is_simulation=self.is_simulation,
            simulated_source=self.source_video_fallback,
        )

        # 2. Start chat ingestion source
        if self.is_simulation:
            self._chat_source = SimulatedStreamChatSource(
                duration_seconds=300.0,
                baseline_mps=1.5,
            )
        elif self.channel.platform == "twitch":
            channel_login = self.channel.handle or self.channel.channel_name.lower()
            self._chat_source = TwitchIRCChatSource(channel=channel_login)
        else:
            self._chat_source = YouTubeLiveChatSource(video_id_or_url=stream_url)

        # 3. Spawn background workers
        self._monitor_tasks.append(asyncio.create_task(self._chat_loop()))
        self._monitor_tasks.append(asyncio.create_task(self._audio_analytics_loop()))
        self._monitor_tasks.append(asyncio.create_task(self._heartbeat_loop()))

        logger.info("LiveStreamMonitorSession started for %s (%s)", self.channel.channel_name, self.session_id)
        return self.state

    async def stop(self) -> LiveSessionState:
        """Gracefully halts all capture, chat sources, and processing."""
        self._is_running = False
        self.state.status = "STOPPED"
        self.state.stopped_at = datetime.now(timezone.utc).isoformat()

        if self._chat_source:
            await self._chat_source.disconnect()

        await self.live_buffer.stop()

        for t in self._monitor_tasks:
            t.cancel()
        self._monitor_tasks.clear()

        self.storage.update_session(self.state)
        logger.info("LiveStreamMonitorSession stopped for %s", self.session_id)
        return self.state

    async def _chat_loop(self) -> None:
        """Consumes live chat messages, feeds spike detector, and handles triggers."""
        try:
            if not self._chat_source:
                return
            await self._chat_source.connect()
            async for msg in self._chat_source.iter_messages():
                if not self._is_running:
                    break

                spike_event = self.spike_detector.feed_message(msg)
                if spike_event:
                    asyncio.create_task(self._on_spike_detected(spike_event))

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("Error in live chat loop (%s): %s", self.session_id, exc)
            self.state.error_message = str(exc)

    async def _audio_analytics_loop(self) -> None:
        """Periodically checks latest audio buffer chunks for energy/loudness surges."""
        while self._is_running:
            await asyncio.sleep(6.0)
            try:
                # Find latest completed chunk
                if self.live_buffer._chunks:
                    latest_chunk = self.live_buffer._chunks[-1]
                    mean_db, max_db, score = self.audio_monitor.analyze_audio_segment(latest_chunk.file_path)
                    self.state.current_audio_db = round(mean_db, 1)
            except Exception as exc:
                logger.debug("Audio analysis error: %s", exc)

    async def _heartbeat_loop(self) -> None:
        """Periodically updates telemetry, rolling demand scores, and database state."""
        while self._is_running:
            await asyncio.sleep(5.0)
            try:
                metrics_10s = self.spike_detector.get_current_metrics(10.0)
                self.state.current_mps = round(metrics_10s.messages_per_second, 2)
                self.state.buffer_duration_seconds = round(self.live_buffer.current_buffer_duration, 1)

                # Clean recent spikes older than 10 minutes
                now_t = time.time()
                self._recent_spikes = [t for t in self._recent_spikes if now_t - t < 600.0]

                # Update composite demand score
                breakdown = self.demand_engine.compute_demand_score(
                    current_viewers=self.state.current_viewers,
                    viewer_history=self.channel.viewer_history,
                    chat_mps=self.state.current_mps,
                    unique_chatters_ratio=metrics_10s.unique_users_ratio,
                    recent_spikes_count=len(self._recent_spikes),
                    audio_excitement_score=getattr(self.audio_monitor, "latest_excitement", 30.0),
                    is_live=True,
                )
                self.state.current_demand_score = breakdown.total_score
                self.storage.update_session(self.state)
            except Exception as exc:
                logger.debug("Heartbeat error: %s", exc)

    async def _on_spike_detected(self, spike: SpikeEvent) -> None:
        """
        Handles confirmed hype burst from the spike detector.
        Fuses multi-modal signals and schedules automatic clip extraction.
        """
        now_t = time.time()
        self._recent_spikes.append(now_t)
        self.state.spikes_detected_count += 1

        # 1. Anti-Duplicate Filtering: enforce minimum distance between extracted clips
        if abs(spike.peak_time - self._last_clip_peak_time) < self.config.dedup_window_seconds:
            logger.info("Skipping overlapping spike at %.1fs (within %ds dedup window)",
                        spike.peak_time, self.config.dedup_window_seconds)
            return

        # 2. Combined Confidence Scoring
        chat_score = spike.highlight_score  # 0-100 from Shannon entropy & z-score
        audio_score = getattr(self.audio_monitor, "latest_excitement", 40.0)
        viewer_factor = min(100.0, max(20.0, self.state.current_demand_score))

        # Weighted combination: 50% Chat + 25% Audio + 25% Demand/Viewers
        combined_confidence = round(
            (0.50 * chat_score) + (0.25 * audio_score) + (0.25 * viewer_factor), 1
        )

        logger.info(
            "Viral moment candidate at %.1fs: ChatScore=%.1f, Audio=%.1f, Combined=%.1f (threshold=%.1f)",
            spike.peak_time, chat_score, audio_score, combined_confidence, self.config.min_confidence
        )

        if combined_confidence < self.config.min_confidence:
            logger.info("Spike confidence %.1f below threshold %.1f; skipping extraction",
                        combined_confidence, self.config.min_confidence)
            return

        self._last_clip_peak_time = spike.peak_time

        # 3. Asynchronously extract clip once post-roll footage has arrived
        asyncio.create_task(self._extract_viral_clip(spike, combined_confidence))

    async def _extract_viral_clip(self, spike: SpikeEvent, confidence: float) -> None:
        """
        Waits for post-roll buffer, extracts raw video slice, renders 9:16 Short,
        and saves into review queue.
        """
        clip_start = max(0.0, spike.clip_start)
        clip_end = spike.clip_end

        logger.info("Queuing viral clip extraction [%.1fs -> %.1fs] (peak: %.1fs)",
                    clip_start, clip_end, spike.peak_time)

        # Wait for post-roll video to be captured in the rolling buffer
        # In simulation with local fallback, we don't need to block if video already exists
        if not self.source_video_fallback or not self.source_video_fallback.exists():
            post_roll_ready = await self.live_buffer.wait_for_post_roll(
                target_timestamp=clip_end,
                timeout_seconds=self.config.post_roll_seconds + 30.0,
            )
            if not post_roll_ready:
                logger.warning("Post-roll buffer wait timed out for spike at %.1fs", spike.peak_time)

        raw_clip_path = OUTPUTS_DIR / f"{self.session_id}_{int(spike.peak_time)}_raw.mp4"

        try:
            # Extract raw video slice
            self.live_buffer.extract_segment(
                start_seconds=clip_start,
                end_seconds=clip_end,
                output_path=raw_clip_path,
                full_source_fallback=self.source_video_fallback,
            )

            # Process into 9:16 YouTube Short with subtitles and viral intelligence
            clip_record = self.clip_pipeline.process_viral_moment(
                raw_clip_path=raw_clip_path,
                session_id=self.session_id,
                channel_name=self.channel.channel_name,
                platform=self.channel.platform,
                stream_url=self.channel.stream_url or self.channel.channel_url,
                peak_time=spike.peak_time,
                clip_start=clip_start,
                clip_end=clip_end,
                confidence_score=confidence,
                demand_score=self.state.current_demand_score,
                config=self.config,
            )

            # Store in database review queue
            self.storage.add_clip(clip_record)
            self.state.clips_extracted_count += 1
            self.storage.update_session(self.state)
            logger.info("Successfully produced viral Short '%s' (%s)", clip_record.title, clip_record.clip_id)

        except Exception as exc:
            logger.error("Failed to extract and process viral clip: %s", exc, exc_info=True)
