from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from .models import ChannelProfile, LiveSessionState, ViralClipRecord

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DEFAULT_DB = DATA / "autoclip.sqlite3"


@contextmanager
def get_trend_db(db_path: Path = DEFAULT_DB) -> Generator[sqlite3.Connection, None, None]:
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


def init_trend_tables(db_path: Path = DEFAULT_DB) -> None:
    with get_trend_db(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trend_channels (
                id TEXT PRIMARY KEY,
                channel_name TEXT NOT NULL,
                platform TEXT NOT NULL,
                channel_url TEXT NOT NULL,
                handle TEXT,
                stream_url TEXT,
                is_live INTEGER NOT NULL DEFAULT 0,
                current_viewers INTEGER NOT NULL DEFAULT 0,
                viewer_history_json TEXT,
                viewer_growth_rate REAL NOT NULL DEFAULT 0.0,
                demand_score REAL NOT NULL DEFAULT 0.0,
                category TEXT,
                stream_title TEXT,
                thumbnail_url TEXT,
                last_checked_at TEXT NOT NULL,
                auto_monitor INTEGER NOT NULL DEFAULT 0
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS trend_sessions (
                session_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                channel_name TEXT NOT NULL,
                platform TEXT NOT NULL,
                stream_url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'MONITORING',
                started_at TEXT NOT NULL,
                stopped_at TEXT,
                current_viewers INTEGER NOT NULL DEFAULT 0,
                current_mps REAL NOT NULL DEFAULT 0.0,
                current_demand_score REAL NOT NULL DEFAULT 0.0,
                spikes_detected_count INTEGER NOT NULL DEFAULT 0,
                clips_extracted_count INTEGER NOT NULL DEFAULT 0,
                buffer_dir TEXT,
                meta_json TEXT
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS trend_clips (
                clip_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                channel_name TEXT NOT NULL,
                platform TEXT NOT NULL,
                stream_url TEXT NOT NULL,
                peak_time REAL NOT NULL,
                clip_start REAL NOT NULL,
                clip_end REAL NOT NULL,
                duration REAL NOT NULL,
                confidence_score REAL NOT NULL,
                demand_score REAL NOT NULL,
                raw_clip_path TEXT,
                shorts_clip_path TEXT,
                thumbnail_path TEXT,
                title TEXT NOT NULL,
                titles_json TEXT,
                description TEXT,
                tags_json TEXT,
                transcript TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
                copyright_confirmed INTEGER NOT NULL DEFAULT 0,
                youtube_video_id TEXT,
                created_at TEXT NOT NULL
            );
        """)


class TrendStorage:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        self.db_path = db_path
        init_trend_tables(self.db_path)

    def upsert_channel(self, channel: ChannelProfile) -> None:
        with get_trend_db(self.db_path) as conn:
            conn.execute("""
                INSERT INTO trend_channels (
                    id, channel_name, platform, channel_url, handle, stream_url,
                    is_live, current_viewers, viewer_history_json, viewer_growth_rate,
                    demand_score, category, stream_title, thumbnail_url, last_checked_at, auto_monitor
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    channel_name = excluded.channel_name,
                    platform = excluded.platform,
                    channel_url = excluded.channel_url,
                    handle = excluded.handle,
                    stream_url = excluded.stream_url,
                    is_live = excluded.is_live,
                    current_viewers = excluded.current_viewers,
                    viewer_history_json = excluded.viewer_history_json,
                    viewer_growth_rate = excluded.viewer_growth_rate,
                    demand_score = excluded.demand_score,
                    category = excluded.category,
                    stream_title = excluded.stream_title,
                    thumbnail_url = excluded.thumbnail_url,
                    last_checked_at = excluded.last_checked_at,
                    auto_monitor = excluded.auto_monitor;
            """, (
                channel.id, channel.channel_name, channel.platform, channel.channel_url,
                channel.handle, channel.stream_url, 1 if channel.is_live else 0,
                channel.current_viewers, json.dumps(channel.viewer_history),
                channel.viewer_growth_rate, channel.demand_score, channel.category,
                channel.stream_title, channel.thumbnail_url, channel.last_checked_at,
                1 if channel.auto_monitor else 0,
            ))

    def get_channel(self, channel_id: str) -> ChannelProfile | None:
        with get_trend_db(self.db_path) as conn:
            row = conn.execute("SELECT * FROM trend_channels WHERE id = ?", (channel_id,)).fetchone()
            if not row:
                return None
            return self._row_to_channel(row)

    def list_channels(self) -> list[ChannelProfile]:
        with get_trend_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM trend_channels ORDER BY is_live DESC, demand_score DESC, current_viewers DESC"
            ).fetchall()
            return [self._row_to_channel(r) for r in rows]

    def delete_channel(self, channel_id: str) -> bool:
        with get_trend_db(self.db_path) as conn:
            res = conn.execute("DELETE FROM trend_channels WHERE id = ?", (channel_id,))
            return res.rowcount > 0

    def create_session(self, session: LiveSessionState, buffer_dir: str = "") -> None:
        with get_trend_db(self.db_path) as conn:
            conn.execute("""
                INSERT INTO trend_sessions (
                    session_id, channel_id, channel_name, platform, stream_url,
                    status, started_at, current_viewers, current_mps, current_demand_score,
                    spikes_detected_count, clips_extracted_count, buffer_dir
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session.session_id, session.channel_id, session.channel_name, session.platform,
                session.stream_url, session.status, session.started_at, session.current_viewers,
                session.current_mps, session.current_demand_score, session.spikes_detected_count,
                session.clips_extracted_count, buffer_dir,
            ))

    def update_session(self, session: LiveSessionState) -> None:
        with get_trend_db(self.db_path) as conn:
            conn.execute("""
                UPDATE trend_sessions SET
                    status = ?, stopped_at = ?, current_viewers = ?, current_mps = ?,
                    current_demand_score = ?, spikes_detected_count = ?, clips_extracted_count = ?
                WHERE session_id = ?
            """, (
                session.status, session.stopped_at, session.current_viewers, session.current_mps,
                session.current_demand_score, session.spikes_detected_count, session.clips_extracted_count,
                session.session_id,
            ))

    def list_sessions(self, active_only: bool = False) -> list[dict[str, Any]]:
        with get_trend_db(self.db_path) as conn:
            query = "SELECT * FROM trend_sessions"
            if active_only:
                query += " WHERE status = 'MONITORING'"
            query += " ORDER BY started_at DESC"
            rows = conn.execute(query).fetchall()
            return [dict(r) for r in rows]

    def add_clip(self, clip: ViralClipRecord) -> None:
        with get_trend_db(self.db_path) as conn:
            conn.execute("""
                INSERT INTO trend_clips (
                    clip_id, session_id, channel_name, platform, stream_url,
                    peak_time, clip_start, clip_end, duration, confidence_score,
                    demand_score, raw_clip_path, shorts_clip_path, thumbnail_path,
                    title, titles_json, description, tags_json, transcript,
                    status, copyright_confirmed, youtube_video_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                clip.clip_id, clip.session_id, clip.channel_name, clip.platform, clip.stream_url,
                clip.peak_time, clip.clip_start, clip.clip_end, clip.duration, clip.confidence_score,
                clip.demand_score, clip.raw_clip_path, clip.shorts_clip_path, clip.thumbnail_path,
                clip.title, json.dumps(clip.titles), clip.description, json.dumps(clip.tags),
                clip.transcript, clip.status, 1 if clip.copyright_confirmed else 0,
                clip.youtube_video_id, clip.created_at,
            ))

    def get_clip(self, clip_id: str) -> ViralClipRecord | None:
        with get_trend_db(self.db_path) as conn:
            row = conn.execute("SELECT * FROM trend_clips WHERE clip_id = ?", (clip_id,)).fetchone()
            if not row:
                return None
            return self._row_to_clip(row)

    def list_clips(self, status: str | None = None, limit: int = 50) -> list[ViralClipRecord]:
        with get_trend_db(self.db_path) as conn:
            query = "SELECT * FROM trend_clips"
            params: list[Any] = []
            if status:
                query += " WHERE status = ?"
                params.append(status)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_clip(r) for r in rows]

    def update_clip_status(
        self,
        clip_id: str,
        status: str,
        copyright_confirmed: bool | None = None,
        youtube_video_id: str | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> bool:
        with get_trend_db(self.db_path) as conn:
            updates = ["status = ?"]
            params: list[Any] = [status]
            if copyright_confirmed is not None:
                updates.append("copyright_confirmed = ?")
                params.append(1 if copyright_confirmed else 0)
            if youtube_video_id is not None:
                updates.append("youtube_video_id = ?")
                params.append(youtube_video_id)
            if title is not None:
                updates.append("title = ?")
                params.append(title)
            if description is not None:
                updates.append("description = ?")
                params.append(description)

            params.append(clip_id)
            sql = f"UPDATE trend_clips SET {', '.join(updates)} WHERE clip_id = ?"
            res = conn.execute(sql, params)
            return res.rowcount > 0

    def delete_clip(self, clip_id: str) -> bool:
        with get_trend_db(self.db_path) as conn:
            res = conn.execute("DELETE FROM trend_clips WHERE clip_id = ?", (clip_id,))
            return res.rowcount > 0

    def _row_to_channel(self, r: sqlite3.Row) -> ChannelProfile:
        history = []
        if r["viewer_history_json"]:
            try:
                history = json.loads(r["viewer_history_json"])
            except Exception:
                pass
        return ChannelProfile(
            id=r["id"],
            channel_name=r["channel_name"],
            platform=r["platform"],
            channel_url=r["channel_url"],
            handle=r["handle"] or "",
            stream_url=r["stream_url"] or "",
            is_live=bool(r["is_live"]),
            current_viewers=r["current_viewers"],
            viewer_history=history,
            viewer_growth_rate=r["viewer_growth_rate"],
            demand_score=r["demand_score"],
            category=r["category"] or "General",
            stream_title=r["stream_title"] or "",
            thumbnail_url=r["thumbnail_url"] or "",
            last_checked_at=r["last_checked_at"],
            auto_monitor=bool(r["auto_monitor"]),
        )

    def _row_to_clip(self, r: sqlite3.Row) -> ViralClipRecord:
        titles = []
        if r["titles_json"]:
            try:
                titles = json.loads(r["titles_json"])
            except Exception:
                pass
        tags = []
        if r["tags_json"]:
            try:
                tags = json.loads(r["tags_json"])
            except Exception:
                pass
        return ViralClipRecord(
            clip_id=r["clip_id"],
            session_id=r["session_id"],
            channel_name=r["channel_name"],
            platform=r["platform"],
            stream_url=r["stream_url"],
            peak_time=r["peak_time"],
            clip_start=r["clip_start"],
            clip_end=r["clip_end"],
            duration=r["duration"],
            confidence_score=r["confidence_score"],
            demand_score=r["demand_score"],
            raw_clip_path=r["raw_clip_path"] or "",
            shorts_clip_path=r["shorts_clip_path"] or "",
            thumbnail_path=r["thumbnail_path"] or "",
            title=r["title"],
            titles=titles,
            description=r["description"] or "",
            tags=tags,
            transcript=r["transcript"] or "",
            status=r["status"],
            copyright_confirmed=bool(r["copyright_confirmed"]),
            youtube_video_id=r["youtube_video_id"],
            created_at=r["created_at"],
        )
