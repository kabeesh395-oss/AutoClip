from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import AdaptiveWeights, ClipFeedbackRecord
from .storage import DEFAULT_DB, get_trend_db

logger = logging.getLogger("autoclip.trend_hunter.learning")


def init_learning_tables(db_path: Path = DEFAULT_DB) -> None:
    with get_trend_db(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trend_learning_weights (
                id INTEGER PRIMARY KEY,
                w_chat REAL NOT NULL DEFAULT 0.30,
                w_chatter_growth REAL NOT NULL DEFAULT 0.15,
                w_viewer_growth REAL NOT NULL DEFAULT 0.15,
                w_audio REAL NOT NULL DEFAULT 0.15,
                w_speech_density REAL NOT NULL DEFAULT 0.15,
                w_transcript_keywords REAL NOT NULL DEFAULT 0.10,
                sensitivity_bias REAL NOT NULL DEFAULT 0.0,
                sample_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS clip_feedback_history (
                feedback_id TEXT PRIMARY KEY,
                clip_id TEXT NOT NULL,
                user_action TEXT NOT NULL,
                views_count INTEGER NOT NULL DEFAULT 0,
                likes_count INTEGER NOT NULL DEFAULT 0,
                features_json TEXT,
                notes TEXT,
                created_at TEXT NOT NULL
            );
        """)


class FeedbackLearner:
    """
    Performance Feedback & Adaptive Threshold Learning System.
    Learns from human approvals/rejections and post-publication engagement
    to adjust future multi-signal weights and sensitivity thresholds dynamically.
    """

    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        self.db_path = db_path
        init_learning_tables(self.db_path)
        self.current_weights = self.load_weights()

    def load_weights(self) -> AdaptiveWeights:
        """Loads saved adaptive weights from SQLite or initializes defaults."""
        with get_trend_db(self.db_path) as conn:
            row = conn.execute("SELECT * FROM trend_learning_weights WHERE id = 1").fetchone()
            if not row:
                w = AdaptiveWeights()
                conn.execute("""
                    INSERT INTO trend_learning_weights (
                        id, w_chat, w_chatter_growth, w_viewer_growth, w_audio,
                        w_speech_density, w_transcript_keywords, sensitivity_bias,
                        sample_count, updated_at
                    ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    w.w_chat, w.w_chatter_growth, w.w_viewer_growth, w.w_audio,
                    w.w_speech_density, w.w_transcript_keywords, w.sensitivity_bias,
                    w.sample_count, datetime.now(timezone.utc).isoformat(),
                ))
                return w

            return AdaptiveWeights(
                w_chat=row["w_chat"],
                w_chatter_growth=row["w_chatter_growth"],
                w_viewer_growth=row["w_viewer_growth"],
                w_audio=row["w_audio"],
                w_speech_density=row["w_speech_density"],
                w_transcript_keywords=row["w_transcript_keywords"],
                sensitivity_bias=row["sensitivity_bias"],
                sample_count=row["sample_count"],
            )

    def save_weights(self, weights: AdaptiveWeights) -> None:
        """Persists updated weights into SQLite."""
        with get_trend_db(self.db_path) as conn:
            conn.execute("""
                UPDATE trend_learning_weights SET
                    w_chat = ?, w_chatter_growth = ?, w_viewer_growth = ?,
                    w_audio = ?, w_speech_density = ?, w_transcript_keywords = ?,
                    sensitivity_bias = ?, sample_count = ?, updated_at = ?
                WHERE id = 1
            """, (
                weights.w_chat, weights.w_chatter_growth, weights.w_viewer_growth,
                weights.w_audio, weights.w_speech_density, weights.w_transcript_keywords,
                weights.sensitivity_bias, weights.sample_count, datetime.now(timezone.utc).isoformat(),
            ))
            self.current_weights = weights

    def record_feedback(
        self,
        clip_id: str,
        user_action: str,  # 'APPROVED', 'REJECTED', 'PUBLISHED'
        features: dict[str, float] | None = None,
        views_count: int = 0,
        likes_count: int = 0,
        notes: str = "",
    ) -> ClipFeedbackRecord:
        """
        Records human review or publication metrics and triggers gradient weight adjustment.
        """
        fb_id = f"fb_{uuid.uuid4().hex[:8]}"
        features_dict = features or {}

        record = ClipFeedbackRecord(
            feedback_id=fb_id,
            clip_id=clip_id,
            user_action=user_action.upper(),
            views_count=views_count,
            likes_count=likes_count,
            features=features_dict,
            notes=notes,
        )

        with get_trend_db(self.db_path) as conn:
            conn.execute("""
                INSERT INTO clip_feedback_history (
                    feedback_id, clip_id, user_action, views_count, likes_count,
                    features_json, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.feedback_id, record.clip_id, record.user_action,
                record.views_count, record.likes_count, json.dumps(record.features),
                record.notes, record.created_at,
            ))

        # Perform adaptive weight update
        self._adapt_weights(record)
        return record

    def _adapt_weights(self, feedback: ClipFeedbackRecord) -> None:
        """
        Updates multi-modal weights based on approval/rejection signals:
          - If a clip was REJECTED:
            - If audio was very high but speech was low -> penalize audio weight, increase speech/dialogue weight
            - Adjust sensitivity bias (+0.5 to reduce false positive rate)
          - If a clip was APPROVED/PUBLISHED:
            - Reinforce features that scored highest in this clip
            - Lower sensitivity bias slightly (-0.2) to capture similar moments
        """
        w = self.current_weights
        lr = w.learning_rate
        f = feedback.features

        if feedback.user_action == "REJECTED":
            # Penalize features with high false activation
            audio_score = f.get("audio_excitement_score", 50.0)
            speech_score = f.get("speech_cadence_score", 50.0)

            if audio_score > 70.0 and speech_score < 40.0:
                # False positive shouting / loud noise without meaningful dialogue
                w.w_audio = max(0.05, w.w_audio - (lr * 0.5))
                w.w_speech_density = min(0.35, w.w_speech_density + (lr * 0.5))
                logger.info("Adaptive tuning: dampening audio weight (-%.3f), boosting speech (+%.3f)", lr * 0.5, lr * 0.5)

            # Rejections dampen baseline confidence to filter out marginal false positives
            w.sensitivity_bias = max(-10.0, w.sensitivity_bias - 0.5)

        elif feedback.user_action in ("APPROVED", "PUBLISHED"):
            chat_score = f.get("chat_velocity_score", 50.0)
            speech_score = f.get("speech_cadence_score", 50.0)

            if chat_score > 70.0:
                w.w_chat = min(0.45, w.w_chat + (lr * 0.3))
            if speech_score > 70.0:
                w.w_speech_density = min(0.35, w.w_speech_density + (lr * 0.3))

            # Reward high engagement performance
            if feedback.views_count > 1000:
                w.w_chatter_growth = min(0.25, w.w_chatter_growth + (lr * 0.2))

            # Approvals boost future confidence for similar high-energy moments
            w.sensitivity_bias = min(10.0, w.sensitivity_bias + 0.3)

        # Normalize weights so they sum to 1.0
        total = w.w_chat + w.w_chatter_growth + w.w_viewer_growth + w.w_audio + w.w_speech_density + w.w_transcript_keywords
        if total > 0:
            w.w_chat /= total
            w.w_chatter_growth /= total
            w.w_viewer_growth /= total
            w.w_audio /= total
            w.w_speech_density /= total
            w.w_transcript_keywords /= total

        w.sample_count += 1
        self.save_weights(w)

    def fetch_youtube_analytics_for_published_clips(self) -> int:
        """
        When authorized YouTube credentials exist, fetches latest public view
        and like metrics for published clips and records performance feedback.
        """
        from ..storage import DEFAULT_DB as APP_DB
        from ..youtube import youtube_status, TOKEN_FILE

        yt_stat = youtube_status()
        if not yt_stat.get("connected") or not TOKEN_FILE.exists():
            return 0

        updated_count = 0
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))
            yt = build("youtube", "v3", credentials=creds, cache_discovery=False)

            with get_trend_db(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT clip_id, youtube_video_id FROM trend_clips WHERE status = 'PUBLISHED' AND youtube_video_id IS NOT NULL"
                ).fetchall()

                video_ids = [r["youtube_video_id"] for r in rows if r["youtube_video_id"] and r["youtube_video_id"] != "published"]
                if not video_ids:
                    return 0

                res = yt.videos().list(part="statistics", id=",".join(video_ids[:50])).execute()
                stats_map = {item["id"]: item.get("statistics", {}) for item in res.get("items", [])}

                for r in rows:
                    vid = r["youtube_video_id"]
                    if vid in stats_map:
                        st = stats_map[vid]
                        views = int(st.get("viewCount", 0))
                        likes = int(st.get("likeCount", 0))
                        conn.execute(
                            "UPDATE trend_clips SET views_count = ?, likes_count = ? WHERE clip_id = ?",
                            (views, likes, r["clip_id"])
                        )
                        # Record feedback
                        self.record_feedback(
                            clip_id=r["clip_id"],
                            user_action="PUBLISHED",
                            views_count=views,
                            likes_count=likes,
                        )
                        updated_count += 1

        except Exception as exc:
            logger.debug("Failed to query YouTube video statistics: %s", exc)

        return updated_count
