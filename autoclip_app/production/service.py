from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analytics import AnalyticsEngine
from .calendar import CalendarManager
from .content_producer import ContentProducer, compute_file_sha256
from .models import ContentItem, PlatformType, PrivacyStatus, ProductionRules, PublishSchedule, PublishStatus
from .multi_publisher import MultiPublisherRouter, PublisherError, QuotaExceededError
from .storage import DEFAULT_DB, ProductionStorage

logger = logging.getLogger("autoclip.production.service")

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUTPUTS = DATA / "outputs"


class ProductionService:
    """
    Unified Production & Autonomous Publishing Service for AutoClip AI.
    Integrates Content Production, Calendar Management, Multi-Platform Publishing,
    and Post-Publication Performance Feedback.
    """

    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        self.db_path = db_path
        self.storage = ProductionStorage(db_path)
        self.producer = ContentProducer()
        self.publisher = MultiPublisherRouter(self.storage)
        self.calendar = CalendarManager(self.storage)
        self.analytics = AnalyticsEngine(self.storage)

        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start_worker(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="ProductionWorker")
        self._worker_thread.start()
        logger.info("Production worker started.")

    def stop_worker(self) -> None:
        self._stop_event.set()
        if self._worker_thread:
            self._worker_thread.join(timeout=3.0)
            self._worker_thread = None
            logger.info("Production worker stopped.")

    def _scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._process_due_schedules()
            except Exception as exc:
                logger.error("Error in production scheduler loop: %s", exc)
            self._stop_event.wait(30.0)

    def _process_due_schedules(self) -> None:
        pending = self.storage.list_pending_schedules()
        rules = self.storage.get_rules()

        for sched in pending:
            # If auto-schedule is disabled, skip processing
            if not rules.auto_schedule_enabled:
                continue

            # Rights Confirmation Guardrail: Never publish without confirmed rights
            if rules.require_rights_confirmation and not sched.rights_confirmed:
                continue

            try:
                logger.info("Auto-publishing due schedule %s (%s)...", sched.id, sched.platform)
                self.publisher.publish_schedule(sched.id)
            except Exception as exc:
                logger.warning("Failed to publish due schedule %s: %s", sched.id, exc)

    def produce_content_from_clip(
        self,
        clip_id: str,
        title: str | None = None,
        description: str | None = None,
        render_variants: bool = True,
    ) -> ContentItem:
        """
        Creates a ContentItem from an existing viral clip record (from trend_clips or clips),
        generating multi-title options, vertical 9:16 & horizontal 16:9 renders, and thumbnail.
        """
        # 1. Search for clip in trend_clips or clips table
        video_path_str: str | None = None
        channel_name: str = "Streamer"
        stream_title: str = "Live Moment"
        duration_sec: float = 30.0
        dialogue_score: float = 85.0
        transcript: str = ""

        with self.storage.get_production_db(self.db_path) as conn:
            # Try trend_clips table
            row = conn.execute("SELECT * FROM trend_clips WHERE id = ?;", (clip_id,)).fetchone()
            if row:
                video_path_str = row["output_path"]
                channel_name = row["channel_name"] if "channel_name" in row.keys() else "Streamer"
                duration_sec = float(row["duration"]) if "duration" in row.keys() else 30.0
                transcript = row["transcript"] if "transcript" in row.keys() else ""
                dialogue_score = float(row["dialogue_score"]) if "dialogue_score" in row.keys() else 85.0
            else:
                # Try clips table
                row2 = conn.execute("SELECT * FROM clips WHERE id = ?;", (clip_id,)).fetchone()
                if row2:
                    video_path_str = row2["path"] if "path" in row2.keys() else None
                    duration_sec = float(row2["duration"]) if "duration" in row2.keys() else 30.0
                    transcript = row2["title"] if "title" in row2.keys() else ""

        if not video_path_str or not Path(video_path_str).exists():
            raise FileNotFoundError(f"Source video file not found for clip {clip_id}: {video_path_str}")

        source_video = Path(video_path_str)
        sha256_hash = compute_file_sha256(source_video)

        # 2. Generate titles and descriptions
        meta_gen = self.producer.generate_titles_and_descriptions(
            transcript=transcript,
            channel_name=channel_name,
            stream_title=stream_title,
        )

        chosen_title = title or meta_gen["title"]
        chosen_desc = description or meta_gen["description"]
        title_options = meta_gen["title_options"]
        hashtags = meta_gen["hashtags"]

        item_id = f"item_{uuid.uuid4().hex[:10]}"
        v_vert_path: str | None = None
        v_horiz_path: str | None = None
        thumb_path: str | None = None

        if render_variants:
            out_vert = OUTPUTS / f"{item_id}_vertical.mp4"
            out_horiz = OUTPUTS / f"{item_id}_horizontal.mp4"
            out_thumb = OUTPUTS / f"{item_id}_thumb.jpg"

            try:
                self.producer.render_vertical_variant(source_video, out_vert)
                v_vert_path = str(out_vert)
            except Exception as e:
                logger.warning("Could not render vertical variant: %s", e)
                v_vert_path = str(source_video)

            try:
                self.producer.render_horizontal_variant(source_video, out_horiz)
                v_horiz_path = str(out_horiz)
            except Exception as e:
                logger.warning("Could not render horizontal variant: %s", e)
                v_horiz_path = str(source_video)

            try:
                self.producer.extract_thumbnail(source_video, out_thumb, timestamp_sec=1.0)
                thumb_path = str(out_thumb)
            except Exception as e:
                logger.warning("Could not extract thumbnail: %s", e)
        else:
            v_vert_path = str(source_video)
            v_horiz_path = str(source_video)

        item = ContentItem(
            id=item_id,
            source_clip_id=clip_id,
            title=chosen_title,
            title_options=title_options,
            description=chosen_desc,
            hashtags=hashtags,
            video_path_vertical=v_vert_path,
            video_path_horizontal=v_horiz_path,
            thumbnail_path=thumb_path,
            duration_seconds=duration_sec,
            dialogue_quality_score=dialogue_score,
            sha256_hash=sha256_hash,
            metadata={
                "channel_name": channel_name,
                "stream_title": stream_title,
                "original_video": str(source_video),
            },
        )
        self.storage.save_content_item(item)
        return item

    def get_global_status(self) -> dict[str, Any]:
        rules = self.storage.get_rules()
        platforms = self.publisher.get_platforms_status()
        summary = self.calendar.get_calendar_view()["summary"]

        return {
            "platforms": platforms,
            "rules": rules.to_dict(),
            "summary": summary,
            "worker_active": self._worker_thread is not None and self._worker_thread.is_alive(),
        }
