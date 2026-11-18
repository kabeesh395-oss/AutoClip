from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from .models import ContentItem, PlatformAnalytics, PlatformType, ProductionRules, PublishSchedule, PublishStatus

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DEFAULT_DB = DATA / "autoclip.sqlite3"

logger = logging.getLogger("autoclip.production.storage")


@contextmanager
def get_production_db(db_path: Path = DEFAULT_DB) -> Generator[sqlite3.Connection, None, None]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _safe_add_column(conn: sqlite3.Connection, table: str, column_def: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def};")
    except sqlite3.OperationalError:
        pass


def init_production_tables(db_path: Path = DEFAULT_DB) -> None:
    with get_production_db(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS production_content_items (
                id TEXT PRIMARY KEY,
                source_clip_id TEXT,
                title TEXT NOT NULL,
                title_options_json TEXT,
                description TEXT,
                hashtags_json TEXT,
                video_path_vertical TEXT,
                video_path_horizontal TEXT,
                thumbnail_path TEXT,
                duration_seconds REAL NOT NULL DEFAULT 0.0,
                dialogue_quality_score REAL NOT NULL DEFAULT 100.0,
                sha256_hash TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS production_schedules (
                id TEXT PRIMARY KEY,
                item_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                scheduled_time_iso TEXT NOT NULL,
                privacy_status TEXT NOT NULL DEFAULT 'private',
                status TEXT NOT NULL DEFAULT 'scheduled',
                external_id TEXT,
                external_url TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 3,
                error_message TEXT,
                rights_confirmed INTEGER NOT NULL DEFAULT 0,
                published_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (item_id) REFERENCES production_content_items(id) ON DELETE CASCADE
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS production_analytics (
                id TEXT PRIMARY KEY,
                item_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                external_id TEXT NOT NULL,
                views INTEGER NOT NULL DEFAULT 0,
                likes INTEGER NOT NULL DEFAULT 0,
                comments INTEGER NOT NULL DEFAULT 0,
                shares INTEGER NOT NULL DEFAULT 0,
                watch_time_seconds REAL NOT NULL DEFAULT 0.0,
                retention_rate REAL NOT NULL DEFAULT 0.0,
                engagement_rate REAL NOT NULL DEFAULT 0.0,
                raw_metrics_json TEXT,
                last_synced_at TEXT NOT NULL,
                FOREIGN KEY (item_id) REFERENCES production_content_items(id) ON DELETE CASCADE
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS production_rules (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                daily_limit_shorts INTEGER NOT NULL DEFAULT 3,
                daily_limit_longform INTEGER NOT NULL DEFAULT 1,
                daily_limit_reels INTEGER NOT NULL DEFAULT 3,
                preferred_slots_json TEXT NOT NULL,
                min_quality_score REAL NOT NULL DEFAULT 60.0,
                require_rights_confirmation INTEGER NOT NULL DEFAULT 1,
                full_auto_enabled INTEGER NOT NULL DEFAULT 0,
                auto_schedule_enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );
        """)

        # Insert default production rules if not present
        row = conn.execute("SELECT id FROM production_rules WHERE id = 1;").fetchone()
        if not row:
            conn.execute("""
                INSERT INTO production_rules (
                    id, daily_limit_shorts, daily_limit_longform, daily_limit_reels,
                    preferred_slots_json, min_quality_score, require_rights_confirmation,
                    full_auto_enabled, auto_schedule_enabled, updated_at
                ) VALUES (1, 3, 1, 3, ?, 60.0, 1, 0, 1, ?);
            """, (json.dumps(["12:00", "16:00", "20:00"]), datetime.now(timezone.utc).isoformat()))


class ProductionStorage:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        self.db_path = db_path
        init_production_tables(self.db_path)

    # -------------------------------------------------------------------------
    # Content Items
    # -------------------------------------------------------------------------
    def save_content_item(self, item: ContentItem) -> None:
        with get_production_db(self.db_path) as conn:
            conn.execute("""
                INSERT INTO production_content_items (
                    id, source_clip_id, title, title_options_json, description,
                    hashtags_json, video_path_vertical, video_path_horizontal,
                    thumbnail_path, duration_seconds, dialogue_quality_score,
                    sha256_hash, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_clip_id=excluded.source_clip_id,
                    title=excluded.title,
                    title_options_json=excluded.title_options_json,
                    description=excluded.description,
                    hashtags_json=excluded.hashtags_json,
                    video_path_vertical=excluded.video_path_vertical,
                    video_path_horizontal=excluded.video_path_horizontal,
                    thumbnail_path=excluded.thumbnail_path,
                    duration_seconds=excluded.duration_seconds,
                    dialogue_quality_score=excluded.dialogue_quality_score,
                    sha256_hash=excluded.sha256_hash,
                    metadata_json=excluded.metadata_json;
            """, (
                item.id,
                item.source_clip_id,
                item.title,
                json.dumps(item.title_options),
                item.description,
                json.dumps(item.hashtags),
                item.video_path_vertical,
                item.video_path_horizontal,
                item.thumbnail_path,
                item.duration_seconds,
                item.dialogue_quality_score,
                item.sha256_hash,
                json.dumps(item.metadata),
                item.created_at,
            ))

    def get_content_item(self, item_id: str) -> ContentItem | None:
        with get_production_db(self.db_path) as conn:
            row = conn.execute("SELECT * FROM production_content_items WHERE id = ?;", (item_id,)).fetchone()
            if not row:
                return None
            return ContentItem(
                id=row["id"],
                source_clip_id=row["source_clip_id"],
                title=row["title"],
                title_options=json.loads(row["title_options_json"] or "[]"),
                description=row["description"] or "",
                hashtags=json.loads(row["hashtags_json"] or "[]"),
                video_path_vertical=row["video_path_vertical"],
                video_path_horizontal=row["video_path_horizontal"],
                thumbnail_path=row["thumbnail_path"],
                duration_seconds=float(row["duration_seconds"]),
                dialogue_quality_score=float(row["dialogue_quality_score"]),
                sha256_hash=row["sha256_hash"],
                metadata=json.loads(row["metadata_json"] or "{}"),
                created_at=row["created_at"],
            )

    def list_content_items(self, limit: int = 50) -> list[ContentItem]:
        with get_production_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM production_content_items ORDER BY created_at DESC LIMIT ?;",
                (limit,)
            ).fetchall()
            items = []
            for row in rows:
                items.append(ContentItem(
                    id=row["id"],
                    source_clip_id=row["source_clip_id"],
                    title=row["title"],
                    title_options=json.loads(row["title_options_json"] or "[]"),
                    description=row["description"] or "",
                    hashtags=json.loads(row["hashtags_json"] or "[]"),
                    video_path_vertical=row["video_path_vertical"],
                    video_path_horizontal=row["video_path_horizontal"],
                    thumbnail_path=row["thumbnail_path"],
                    duration_seconds=float(row["duration_seconds"]),
                    dialogue_quality_score=float(row["dialogue_quality_score"]),
                    sha256_hash=row["sha256_hash"],
                    metadata=json.loads(row["metadata_json"] or "{}"),
                    created_at=row["created_at"],
                ))
            return items

    # -------------------------------------------------------------------------
    # Schedules
    # -------------------------------------------------------------------------
    def create_schedule(self, sched: PublishSchedule) -> None:
        with get_production_db(self.db_path) as conn:
            conn.execute("""
                INSERT INTO production_schedules (
                    id, item_id, platform, scheduled_time_iso, privacy_status,
                    status, external_id, external_url, retry_count, max_retries,
                    error_message, rights_confirmed, published_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    item_id=excluded.item_id,
                    platform=excluded.platform,
                    scheduled_time_iso=excluded.scheduled_time_iso,
                    privacy_status=excluded.privacy_status,
                    status=excluded.status,
                    external_id=excluded.external_id,
                    external_url=excluded.external_url,
                    retry_count=excluded.retry_count,
                    max_retries=excluded.max_retries,
                    error_message=excluded.error_message,
                    rights_confirmed=excluded.rights_confirmed,
                    published_at=excluded.published_at,
                    updated_at=excluded.updated_at;
            """, (
                sched.id,
                sched.item_id,
                sched.platform,
                sched.scheduled_time_iso,
                sched.privacy_status,
                sched.status,
                sched.external_id,
                sched.external_url,
                sched.retry_count,
                sched.max_retries,
                sched.error_message,
                1 if sched.rights_confirmed else 0,
                sched.published_at,
                sched.created_at,
                sched.updated_at,
            ))

    def get_schedule(self, schedule_id: str) -> PublishSchedule | None:
        with get_production_db(self.db_path) as conn:
            row = conn.execute("SELECT * FROM production_schedules WHERE id = ?;", (schedule_id,)).fetchone()
            if not row:
                return None
            return PublishSchedule(
                id=row["id"],
                item_id=row["item_id"],
                platform=row["platform"],
                scheduled_time_iso=row["scheduled_time_iso"],
                privacy_status=row["privacy_status"],
                status=row["status"],
                external_id=row["external_id"],
                external_url=row["external_url"],
                retry_count=int(row["retry_count"]),
                max_retries=int(row["max_retries"]),
                error_message=row["error_message"],
                rights_confirmed=bool(row["rights_confirmed"]),
                published_at=row["published_at"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def update_schedule(self, sched: PublishSchedule) -> None:
        sched.updated_at = datetime.now(timezone.utc).isoformat()
        self.create_schedule(sched)

    def list_schedules(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        platform: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[PublishSchedule]:
        with get_production_db(self.db_path) as conn:
            query = "SELECT * FROM production_schedules WHERE 1=1"
            params: list[Any] = []
            if start_date:
                query += " AND scheduled_time_iso >= ?"
                params.append(start_date)
            if end_date:
                query += " AND scheduled_time_iso <= ?"
                params.append(end_date)
            if platform:
                query += " AND platform = ?"
                params.append(platform)
            if status:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY scheduled_time_iso ASC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            schedules = []
            for row in rows:
                schedules.append(PublishSchedule(
                    id=row["id"],
                    item_id=row["item_id"],
                    platform=row["platform"],
                    scheduled_time_iso=row["scheduled_time_iso"],
                    privacy_status=row["privacy_status"],
                    status=row["status"],
                    external_id=row["external_id"],
                    external_url=row["external_url"],
                    retry_count=int(row["retry_count"]),
                    max_retries=int(row["max_retries"]),
                    error_message=row["error_message"],
                    rights_confirmed=bool(row["rights_confirmed"]),
                    published_at=row["published_at"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                ))
            return schedules

    def list_pending_schedules(self, current_time_iso: str | None = None) -> list[PublishSchedule]:
        target_time = current_time_iso or datetime.now(timezone.utc).isoformat()
        with get_production_db(self.db_path) as conn:
            rows = conn.execute("""
                SELECT * FROM production_schedules
                WHERE status = 'scheduled' AND scheduled_time_iso <= ?
                ORDER BY scheduled_time_iso ASC;
            """, (target_time,)).fetchall()
            return [
                PublishSchedule(
                    id=row["id"],
                    item_id=row["item_id"],
                    platform=row["platform"],
                    scheduled_time_iso=row["scheduled_time_iso"],
                    privacy_status=row["privacy_status"],
                    status=row["status"],
                    external_id=row["external_id"],
                    external_url=row["external_url"],
                    retry_count=int(row["retry_count"]),
                    max_retries=int(row["max_retries"]),
                    error_message=row["error_message"],
                    rights_confirmed=bool(row["rights_confirmed"]),
                    published_at=row["published_at"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
                for row in rows
            ]

    def cancel_schedule(self, schedule_id: str) -> bool:
        sched = self.get_schedule(schedule_id)
        if not sched:
            return False
        sched.status = PublishStatus.CANCELLED.value
        self.update_schedule(sched)
        return True

    def reschedule(self, schedule_id: str, new_time_iso: str) -> bool:
        sched = self.get_schedule(schedule_id)
        if not sched:
            return False
        sched.scheduled_time_iso = new_time_iso
        sched.status = PublishStatus.SCHEDULED.value
        self.update_schedule(sched)
        return True

    def retry_schedule(self, schedule_id: str) -> bool:
        sched = self.get_schedule(schedule_id)
        if not sched:
            return False
        sched.status = PublishStatus.SCHEDULED.value
        sched.error_message = None
        self.update_schedule(sched)
        return True

    # -------------------------------------------------------------------------
    # Duplicate Prevention & Limits
    # -------------------------------------------------------------------------
    def is_hash_published(self, sha256_hash: str | None, platform: str, exclude_schedule_id: str | None = None) -> bool:
        if not sha256_hash:
            return False
        with get_production_db(self.db_path) as conn:
            if exclude_schedule_id:
                row = conn.execute("""
                    SELECT s.id FROM production_schedules s
                    JOIN production_content_items c ON s.item_id = c.id
                    WHERE c.sha256_hash = ? AND s.platform = ? AND s.id != ? AND s.status IN ('published', 'uploading', 'scheduled');
                """, (sha256_hash, platform, exclude_schedule_id)).fetchone()
            else:
                row = conn.execute("""
                    SELECT s.id FROM production_schedules s
                    JOIN production_content_items c ON s.item_id = c.id
                    WHERE c.sha256_hash = ? AND s.platform = ? AND s.status IN ('published', 'uploading', 'scheduled');
                """, (sha256_hash, platform)).fetchone()
            return row is not None

    def count_published_today(self, platform: str, date_prefix_utc: str | None = None) -> int:
        prefix = date_prefix_utc or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with get_production_db(self.db_path) as conn:
            row = conn.execute("""
                SELECT COUNT(*) as count FROM production_schedules
                WHERE platform = ? AND status = 'published' AND published_at LIKE ?;
            """, (platform, f"{prefix}%")).fetchone()
            return int(row["count"]) if row else 0

    # -------------------------------------------------------------------------
    # Analytics
    # -------------------------------------------------------------------------
    def save_analytics(self, analytics: PlatformAnalytics) -> None:
        with get_production_db(self.db_path) as conn:
            conn.execute("""
                INSERT INTO production_analytics (
                    id, item_id, platform, external_id, views, likes,
                    comments, shares, watch_time_seconds, retention_rate,
                    engagement_rate, raw_metrics_json, last_synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    views=excluded.views,
                    likes=excluded.likes,
                    comments=excluded.comments,
                    shares=excluded.shares,
                    watch_time_seconds=excluded.watch_time_seconds,
                    retention_rate=excluded.retention_rate,
                    engagement_rate=excluded.engagement_rate,
                    raw_metrics_json=excluded.raw_metrics_json,
                    last_synced_at=excluded.last_synced_at;
            """, (
                analytics.id,
                analytics.item_id,
                analytics.platform,
                analytics.external_id,
                analytics.views,
                analytics.likes,
                analytics.comments,
                analytics.shares,
                analytics.watch_time_seconds,
                analytics.retention_rate,
                analytics.engagement_rate,
                json.dumps(analytics.raw_metrics),
                analytics.last_synced_at,
            ))

    def get_analytics_for_item(self, item_id: str) -> list[PlatformAnalytics]:
        with get_production_db(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM production_analytics WHERE item_id = ?;", (item_id,)).fetchall()
            return [
                PlatformAnalytics(
                    id=row["id"],
                    item_id=row["item_id"],
                    platform=row["platform"],
                    external_id=row["external_id"],
                    views=int(row["views"]),
                    likes=int(row["likes"]),
                    comments=int(row["comments"]),
                    shares=int(row["shares"]),
                    watch_time_seconds=float(row["watch_time_seconds"]),
                    retention_rate=float(row["retention_rate"]),
                    engagement_rate=float(row["engagement_rate"]),
                    raw_metrics=json.loads(row["raw_metrics_json"] or "{}"),
                    last_synced_at=row["last_synced_at"],
                )
                for row in rows
            ]

    def list_all_analytics(self, limit: int = 100) -> list[PlatformAnalytics]:
        with get_production_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM production_analytics ORDER BY last_synced_at DESC LIMIT ?;",
                (limit,)
            ).fetchall()
            return [
                PlatformAnalytics(
                    id=row["id"],
                    item_id=row["item_id"],
                    platform=row["platform"],
                    external_id=row["external_id"],
                    views=int(row["views"]),
                    likes=int(row["likes"]),
                    comments=int(row["comments"]),
                    shares=int(row["shares"]),
                    watch_time_seconds=float(row["watch_time_seconds"]),
                    retention_rate=float(row["retention_rate"]),
                    engagement_rate=float(row["engagement_rate"]),
                    raw_metrics=json.loads(row["raw_metrics_json"] or "{}"),
                    last_synced_at=row["last_synced_at"],
                )
                for row in rows
            ]

    # -------------------------------------------------------------------------
    # Rules
    # -------------------------------------------------------------------------
    def get_rules(self) -> ProductionRules:
        with get_production_db(self.db_path) as conn:
            row = conn.execute("SELECT * FROM production_rules WHERE id = 1;").fetchone()
            if not row:
                return ProductionRules()
            return ProductionRules(
                daily_limit_shorts=int(row["daily_limit_shorts"]),
                daily_limit_longform=int(row["daily_limit_longform"]),
                daily_limit_reels=int(row["daily_limit_reels"]),
                preferred_slots=json.loads(row["preferred_slots_json"] or "[]"),
                min_quality_score=float(row["min_quality_score"]),
                require_rights_confirmation=bool(row["require_rights_confirmation"]),
                full_auto_enabled=bool(row["full_auto_enabled"]),
                auto_schedule_enabled=bool(row["auto_schedule_enabled"]),
            )

    def save_rules(self, rules: ProductionRules) -> None:
        with get_production_db(self.db_path) as conn:
            conn.execute("""
                UPDATE production_rules SET
                    daily_limit_shorts = ?,
                    daily_limit_longform = ?,
                    daily_limit_reels = ?,
                    preferred_slots_json = ?,
                    min_quality_score = ?,
                    require_rights_confirmation = ?,
                    full_auto_enabled = ?,
                    auto_schedule_enabled = ?,
                    updated_at = ?
                WHERE id = 1;
            """, (
                rules.daily_limit_shorts,
                rules.daily_limit_longform,
                rules.daily_limit_reels,
                json.dumps(rules.preferred_slots),
                rules.min_quality_score,
                1 if rules.require_rights_confirmation else 0,
                1 if rules.full_auto_enabled else 0,
                1 if rules.auto_schedule_enabled else 0,
                datetime.now(timezone.utc).isoformat(),
            ))
