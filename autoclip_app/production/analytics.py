from __future__ import annotations

import json
import logging
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import PlatformAnalytics, PlatformType
from .storage import DEFAULT_DB, ProductionStorage

logger = logging.getLogger("autoclip.production.analytics")

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
TOKEN_FILE = DATA / "youtube_token.json"
INSTAGRAM_TOKEN_FILE = DATA / "instagram_token.json"


class AnalyticsEngine:
    """
    Synchronizes authentic post-publication analytics from YouTube and Instagram Graph API,
    calculates engagement metrics, and feeds back into the Trend Hunter FeedbackLearner.
    """

    def __init__(self, storage: ProductionStorage | None = None) -> None:
        self.storage = storage or ProductionStorage()

    def sync_youtube_metrics(self, video_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not video_ids or not TOKEN_FILE.exists():
            return {}

        results: dict[str, dict[str, Any]] = {}
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            from ..youtube import SCOPES

            credentials = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)

            # Query in batches of up to 50
            for i in range(0, len(video_ids), 50):
                batch_ids = video_ids[i:i + 50]
                resp = youtube.videos().list(part="statistics,contentDetails", id=",".join(batch_ids)).execute()
                for item in resp.get("items", []):
                    vid = item["id"]
                    stats = item.get("statistics", {})
                    results[vid] = {
                        "views": int(stats.get("viewCount", 0)),
                        "likes": int(stats.get("likeCount", 0)),
                        "comments": int(stats.get("commentCount", 0)),
                        "shares": 0,
                        "raw": stats,
                    }
        except Exception as exc:
            logger.warning("YouTube analytics sync failed: %s", exc)

        return results

    def sync_instagram_metrics(self, media_ids: list[str]) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        if not media_ids:
            return results

        token = None
        if INSTAGRAM_TOKEN_FILE.exists():
            try:
                data = json.loads(INSTAGRAM_TOKEN_FILE.read_text(encoding="utf-8"))
                token = data.get("access_token")
            except Exception:
                pass

        if not token:
            return results

        for mid in media_ids:
            try:
                url = f"https://graph.facebook.com/v19.0/{mid}?fields=like_count,comments_count&access_token={token}"
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    results[mid] = {
                        "views": 0,  # Reels plays metric requires specific insights endpoint
                        "likes": int(data.get("like_count", 0)),
                        "comments": int(data.get("comments_count", 0)),
                        "shares": 0,
                        "raw": data,
                    }
            except Exception as exc:
                logger.debug("Instagram metrics sync error for %s: %s", mid, exc)

        return results

    def sync_all_analytics(self) -> list[PlatformAnalytics]:
        """
        Fetches updated analytics for all published schedules with external IDs,
        persists them, and triggers adaptive learning updates.
        """
        schedules = self.storage.list_schedules(status="published", limit=200)
        yt_vids = [s.external_id for s in schedules if s.external_id and "youtube" in s.platform]
        ig_mids = [s.external_id for s in schedules if s.external_id and "instagram" in s.platform]

        yt_data = self.sync_youtube_metrics(yt_vids)
        ig_data = self.sync_instagram_metrics(ig_mids)

        updated_records: list[PlatformAnalytics] = []

        # Try to load FeedbackLearner for dynamic tuning
        learner = None
        try:
            from ..trend_hunter.learning import FeedbackLearner
            learner = FeedbackLearner(self.storage.db_path)
        except Exception:
            pass

        for s in schedules:
            if not s.external_id:
                continue

            metrics = None
            if s.external_id in yt_data:
                metrics = yt_data[s.external_id]
            elif s.external_id in ig_data:
                metrics = ig_data[s.external_id]

            if not metrics:
                continue

            views = metrics.get("views", 0)
            likes = metrics.get("likes", 0)
            comments = metrics.get("comments", 0)
            shares = metrics.get("shares", 0)

            # Compute engagement rate: (likes + comments + shares) / max(1, views) * 100
            interactions = likes + comments + shares
            engagement_rate = (interactions / max(1, views)) * 100.0 if views > 0 else 0.0

            pa_id = f"pa_{s.id}"
            record = PlatformAnalytics(
                id=pa_id,
                item_id=s.item_id,
                platform=s.platform,
                external_id=s.external_id,
                views=views,
                likes=likes,
                comments=comments,
                shares=shares,
                watch_time_seconds=0.0,
                retention_rate=0.0,
                engagement_rate=round(engagement_rate, 2),
                raw_metrics=metrics.get("raw", {}),
                last_synced_at=datetime.now(timezone.utc).isoformat(),
            )
            self.storage.save_analytics(record)
            updated_records.append(record)

            # Feed performance into FeedbackLearner
            if learner and s.item_id:
                item = self.storage.get_content_item(s.item_id)
                clip_id = item.source_clip_id if item else s.item_id
                if clip_id:
                    if engagement_rate >= 5.0 or views >= 500:
                        learner.record_feedback(
                            clip_id=clip_id,
                            user_action="PUBLISHED",
                            views_count=views,
                            likes_count=likes,
                            notes=f"Strong engagement {engagement_rate:.2f}% on {s.platform}",
                        )
                    elif views >= 200 and engagement_rate < 1.0:
                        learner.record_feedback(
                            clip_id=clip_id,
                            user_action="REJECTED",
                            views_count=views,
                            likes_count=likes,
                            notes=f"Low engagement {engagement_rate:.2f}% on {s.platform}",
                        )

        return updated_records

    def get_analytics_summary(self) -> dict[str, Any]:
        records = self.storage.list_all_analytics(limit=100)
        total_views = sum(r.views for r in records)
        total_likes = sum(r.likes for r in records)
        total_comments = sum(r.comments for r in records)
        total_shares = sum(r.shares for r in records)
        avg_eng = sum(r.engagement_rate for r in records) / max(1, len(records)) if records else 0.0

        return {
            "total_published_items": len(records),
            "total_views": total_views,
            "total_likes": total_likes,
            "total_comments": total_comments,
            "total_shares": total_shares,
            "average_engagement_rate": round(avg_eng, 2),
            "records": [r.to_dict() for r in records],
        }
