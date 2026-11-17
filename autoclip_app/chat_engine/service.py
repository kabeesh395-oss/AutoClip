from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator

from .clip_extractor import ChatClipExtractor
from .detector import ChatSpikeDetector
from .models import ChatMessage, ChatWindowMetrics, SpikeDetectorConfig, SpikeEvent
from .sources import (
    BaseChatSource,
    FileReplayChatSource,
    SimulatedStreamChatSource,
    TwitchIRCChatSource,
    YouTubeLiveChatSource,
)
from .storage import (
    close_chat_session,
    create_chat_session,
    get_chat_session_summary,
    init_chat_tables,
    insert_chat_message,
    insert_chat_spike,
    list_chat_spikes,
    update_spike_clip_path,
)

logger = logging.getLogger("autoclip.chat.service")


class ChatSpikeEngineService:
    """
    Central orchestration service for real-time live chat spike detection.
    Manages background monitoring tasks, event persistence, and automatic
    clip extraction.
    """

    def __init__(self) -> None:
        init_chat_tables()
        self.active_session_id: str | None = None
        self.source: BaseChatSource | None = None
        self.detector: ChatSpikeDetector | None = None
        self.extractor = ChatClipExtractor()
        self.video_source_path: Path | None = None
        self._monitor_task: asyncio.Task | None = None
        self._recent_mps_history: list[dict[str, float]] = []

    @property
    def is_active(self) -> bool:
        return self._monitor_task is not None and not self._monitor_task.done()

    def get_status(self) -> dict[str, Any]:
        baseline_mean = 0.5
        current_mps = 0.0
        if self.detector:
            b_mean, _ = self.detector.get_baseline_stats()
            baseline_mean = round(b_mean, 2)
            now_t = self.detector._last_baseline_sample_t
            if self.detector._message_buffer:
                now_t = self.detector._message_buffer[-1].timestamp
            m10 = self.detector.get_window_metrics(now_t, 10.0)
            current_mps = round(m10.mps, 2)

        spikes = list_chat_spikes(self.active_session_id, limit=20)
        return {
            "is_active": self.is_active,
            "session_id": self.active_session_id,
            "source_type": self.source.source_name if self.source else "none",
            "current_mps": current_mps,
            "baseline_mps": baseline_mean,
            "total_messages": self.detector.total_messages_received if self.detector else 0,
            "total_spikes": len(spikes),
            "video_attached": str(self.video_source_path) if self.video_source_path else None,
            "recent_spikes": spikes[:5],
        }

    async def start_session(
        self,
        stream_target: str,
        source_type: str = "twitch",
        video_path: str | Path | None = None,
        config: SpikeDetectorConfig | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Starts a live chat monitoring session.
        stream_target: channel name (Twitch), live URL/ID (YouTube), or file path (File)
        """
        if self.is_active:
            await self.stop_session()

        sid = session_id or f"session_{uuid.uuid4().hex[:8]}"
        self.active_session_id = sid
        self.video_source_path = Path(video_path) if video_path else None
        cfg = config or SpikeDetectorConfig()

        create_chat_session(sid, stream_target, source_type, {"video_path": str(video_path) if video_path else None})

        # Initialize detector with callback
        def on_spike(spike: SpikeEvent):
            logger.info("Spike detected! Peak: %s MPS, Confidence: %s", spike.peak_mps, spike.confidence_score)
            insert_chat_spike(spike)
            # If video source is attached, extract clip asynchronously
            if self.video_source_path and self.video_source_path.exists():
                def _extract_task():
                    try:
                        out = self.extractor.extract_clip(self.video_source_path, spike)
                        update_spike_clip_path(spike.spike_id, str(out), "EXTRACTED")
                    except Exception as e:
                        logger.error("Failed to extract clip for spike %s: %s", spike.spike_id, e)
                        update_spike_clip_path(spike.spike_id, "", "FAILED")

                asyncio.create_task(asyncio.to_thread(_extract_task))

        self.detector = ChatSpikeDetector(cfg, session_id=sid, on_spike_callback=on_spike)

        # Create source
        if source_type == "twitch":
            self.source = TwitchIRCChatSource(channel=stream_target)
        elif source_type == "youtube":
            self.source = YouTubeLiveChatSource(video_id_or_url=stream_target)
        elif source_type == "file":
            self.source = FileReplayChatSource(file_path=stream_target, playback_speed=0.0)
        elif source_type == "simulated":
            self.source = SimulatedStreamChatSource(duration_seconds=120.0, instant=True)
        else:
            raise ValueError(f"Unsupported chat source type: {source_type}")

        # Start background consumer
        self._monitor_task = asyncio.create_task(self._run_consumer())
        return self.get_status()

    async def _run_consumer(self) -> None:
        if not self.source or not self.detector or not self.active_session_id:
            return

        from .storage import insert_chat_messages_batch

        logger.info("Starting chat consumer for session %s...", self.active_session_id)
        batch: list[tuple[ChatMessage, bool]] = []
        try:
            await self.source.connect()
            async for msg in self.source.iter_messages():
                spike = self.detector.feed_message(msg)
                batch.append((msg, spike is not None))
                if len(batch) >= 50:
                    insert_chat_messages_batch(self.active_session_id, batch)
                    batch.clear()

            if batch:
                insert_chat_messages_batch(self.active_session_id, batch)
                batch.clear()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Chat consumer error in session %s: %s", self.active_session_id, exc)
        finally:
            if batch and self.active_session_id:
                try:
                    insert_chat_messages_batch(self.active_session_id, batch)
                except Exception:
                    pass
            if self.source:
                await self.source.disconnect()
            if self.active_session_id:
                close_chat_session(self.active_session_id)
            logger.info("Chat consumer finished.")

    async def stop_session(self) -> dict[str, Any]:
        """Stops active monitoring."""
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except Exception:
                pass
            self._monitor_task = None

        if self.source:
            await self.source.disconnect()
            self.source = None

        if self.active_session_id:
            close_chat_session(self.active_session_id)

        res = self.get_status()
        self.active_session_id = None
        return res

    def list_spikes(self, limit: int = 50) -> list[dict[str, Any]]:
        return list_chat_spikes(self.active_session_id, limit=limit)


# Global singleton instance for application use
chat_engine_service = ChatSpikeEngineService()
