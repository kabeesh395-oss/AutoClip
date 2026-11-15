from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BATCH_DB = Path(os.getenv("AUTOCLIP_DB_PATH", str(DATA / "autoclip.sqlite3")))
IST = timezone(timedelta(hours=5, minutes=30))

# All 15 required job states from SRS FR-14
JOB_STATES = (
    "QUEUED",
    "DOWNLOADING",
    "ANALYZING",
    "SELECTING_HIGHLIGHTS",
    "TRANSCRIBING",
    "EDITING",
    "RENDERING",
    "QUALITY_CHECK",
    "RIGHTS_REVIEW",
    "AWAITING_APPROVAL",
    "APPROVED",
    "SCHEDULED",
    "PUBLISHED",
    "FAILED",
    "CANCELLED",
)

# Rights Statuses from SRS FR-10
RIGHTS_STATUSES = ("PASS", "REVIEW_REQUIRED", "BLOCKED")

# Default daily slots (Asia/Kolkata) from SRS FR-12
DEFAULT_SLOTS = [
    {"slot": 1, "time": "12:00:00", "label": "12:00 PM", "format": "SHORT"},
    {"slot": 2, "time": "16:00:00", "label": "04:00 PM", "format": "SHORT"},
    {"slot": 3, "time": "19:00:00", "label": "07:00 PM", "format": "LONG"},
]


def _now_ist_iso() -> str:
    return datetime.now(IST).isoformat()


@contextmanager
def get_db(db_path: Path = BATCH_DB) -> Generator[sqlite3.Connection, None, None]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
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


def init_batch_tables(db_path: Path = BATCH_DB) -> None:
    """Initialize all 8 tables defined in the SRS without breaking existing tables."""
    with get_db(db_path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS creators (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            channel_url TEXT NOT NULL,
            permission_status TEXT NOT NULL DEFAULT 'ACTIVE',
            permission_document TEXT,
            permitted_platforms TEXT NOT NULL DEFAULT 'youtube',
            commercial_use_status TEXT NOT NULL DEFAULT 'ALLOWED',
            attribution_requirements TEXT,
            expiration_date TEXT,
            revoked_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS game_policies (
            game_name TEXT PRIMARY KEY,
            publisher_name TEXT,
            commercial_policy TEXT NOT NULL DEFAULT 'VERIFIED',
            monetization_allowed INTEGER NOT NULL DEFAULT 1,
            verified_at TEXT NOT NULL,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS batches (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'DRAFT',
            target_videos_per_day INTEGER NOT NULL DEFAULT 3,
            schedule_days INTEGER NOT NULL DEFAULT 3,
            batch_target INTEGER NOT NULL DEFAULT 9,
            daily_shorts INTEGER NOT NULL DEFAULT 2,
            daily_long INTEGER NOT NULL DEFAULT 1,
            timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
            language TEXT NOT NULL DEFAULT 'en',
            whisper_model TEXT NOT NULL DEFAULT 'small',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            creator_id TEXT,
            source_url TEXT NOT NULL,
            file_path TEXT,
            title TEXT,
            duration REAL DEFAULT 0.0,
            game_name TEXT,
            rights_status TEXT NOT NULL DEFAULT 'PASS',
            rights_notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE,
            FOREIGN KEY (creator_id) REFERENCES creators(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            source_id TEXT,
            job_type TEXT NOT NULL DEFAULT 'SOURCE_PROCESS',
            status TEXT NOT NULL DEFAULT 'QUEUED',
            progress INTEGER NOT NULL DEFAULT 0,
            stage_message TEXT DEFAULT '',
            retry_count INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 3,
            error_message TEXT DEFAULT '',
            started_at TEXT,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE,
            FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS clips (
            id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            format TEXT NOT NULL DEFAULT 'SHORT',
            start_time REAL NOT NULL,
            end_time REAL NOT NULL,
            duration REAL NOT NULL,
            category TEXT DEFAULT 'Highlight',
            hook_score REAL NOT NULL DEFAULT 0.0,
            title TEXT NOT NULL,
            titles_json TEXT,
            description TEXT,
            hashtags_json TEXT,
            hook_json TEXT,
            scene_intelligence_json TEXT,
            transcript TEXT,
            caption_file TEXT,
            output_path TEXT,
            rights_status TEXT NOT NULL DEFAULT 'PASS',
            approval_status TEXT NOT NULL DEFAULT 'PENDING',
            approved_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE,
            FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS schedules (
            id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            clip_id TEXT NOT NULL,
            slot_number INTEGER NOT NULL,
            scheduled_at TEXT NOT NULL,
            slot_time TEXT NOT NULL,
            format TEXT NOT NULL DEFAULT 'SHORT',
            status TEXT NOT NULL DEFAULT 'PENDING',
            youtube_channel_id TEXT,
            youtube_video_id TEXT,
            youtube_privacy_status TEXT DEFAULT 'public',
            published_at TEXT,
            error_message TEXT,
            FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE,
            FOREIGN KEY (clip_id) REFERENCES clips(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT,
            user_id TEXT DEFAULT 'local_user',
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            details TEXT
        );
        """)

        # Opus Clip columns migration
        for col_def in [
            "virality_grade TEXT DEFAULT 'A'",
            "flow_score REAL DEFAULT 80.0",
            "engagement_score REAL DEFAULT 85.0",
            "trend_score REAL DEFAULT 80.0",
            "layout TEXT DEFAULT 'FIT_BLUR'",
            "caption_style TEXT DEFAULT 'KINETIC_YELLOW'",
            "why_viral TEXT DEFAULT ''",
            "improvement_tips_json TEXT DEFAULT '[]'"
        ]:
            try:
                conn.execute(f"ALTER TABLE clips ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass


def log_audit(action: str, entity_type: str, entity_id: str, batch_id: str | None = None, details: str = "", db_path: Path = BATCH_DB) -> None:
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT INTO audit_logs (batch_id, action, entity_type, entity_id, timestamp, details) VALUES (?, ?, ?, ?, ?, ?)",
            (batch_id, action, entity_type, entity_id, _now_ist_iso(), details)
        )


# ==========================================
# CREATOR & RIGHTS VALIDATION
# ==========================================

def create_creator(
    name: str,
    channel_url: str,
    permission_status: str = "ACTIVE",
    permission_document: str = "",
    permitted_platforms: str = "youtube",
    commercial_use_status: str = "ALLOWED",
    attribution_requirements: str = "",
    expiration_date: str = "",
    creator_id: str | None = None,
    db_path: Path = BATCH_DB
) -> dict[str, Any]:
    cid = creator_id or f"creator_{uuid.uuid4().hex[:8]}"
    now = _now_ist_iso()
    with get_db(db_path) as conn:
        conn.execute("""
            INSERT INTO creators (id, name, channel_url, permission_status, permission_document, permitted_platforms,
                                  commercial_use_status, attribution_requirements, expiration_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (cid, name, channel_url, permission_status, permission_document, permitted_platforms,
              commercial_use_status, attribution_requirements, expiration_date, now))
    log_audit("CREATE_CREATOR", "creator", cid, details=f"Creator: {name}", db_path=db_path)
    return get_creator(cid, db_path=db_path) or {}


def get_creator(creator_id: str, db_path: Path = BATCH_DB) -> dict[str, Any] | None:
    with get_db(db_path) as conn:
        row = conn.execute("SELECT * FROM creators WHERE id = ?", (creator_id,)).fetchone()
        return dict(row) if row else None


def list_creators(db_path: Path = BATCH_DB) -> list[dict[str, Any]]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM (SELECT * FROM creators ORDER BY created_at DESC) GROUP BY name ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def register_game_policy(
    game_name: str,
    publisher_name: str = "",
    commercial_policy: str = "VERIFIED",
    monetization_allowed: int = 1,
    notes: str = "",
    db_path: Path = BATCH_DB
) -> dict[str, Any]:
    now = _now_ist_iso()
    with get_db(db_path) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO game_policies (game_name, publisher_name, commercial_policy, monetization_allowed, verified_at, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (game_name, publisher_name, commercial_policy, monetization_allowed, now, notes))
    log_audit("REGISTER_GAME_POLICY", "game_policy", game_name, details=f"Game: {game_name}, Policy: {commercial_policy}", db_path=db_path)
    return get_game_policy(game_name, db_path=db_path) or {}


def get_game_policy(game_name: str, db_path: Path = BATCH_DB) -> dict[str, Any] | None:
    with get_db(db_path) as conn:
        row = conn.execute("SELECT * FROM game_policies WHERE game_name = ?", (game_name,)).fetchone()
        return dict(row) if row else None


def list_game_policies(db_path: Path = BATCH_DB) -> list[dict[str, Any]]:
    with get_db(db_path) as conn:
        rows = conn.execute("SELECT * FROM game_policies ORDER BY game_name ASC").fetchall()
        return [dict(r) for r in rows]


def validate_source_rights(
    creator_id: str | None = None,
    game_name: str | None = None,
    db_path: Path = BATCH_DB
) -> tuple[str, str]:
    """Validate rights per FR-02, FR-03, and FR-10. Returns (status, notes)."""
    notes = []
    status = "PASS"

    if creator_id:
        creator = get_creator(creator_id, db_path=db_path)
        if not creator:
            return "BLOCKED", "Creator ID not found in verified registry."
        if creator.get("revoked_at"):
            return "BLOCKED", f"Permission revoked for creator {creator['name']} on {creator['revoked_at']}."
        if creator.get("permission_status") != "ACTIVE":
            return "BLOCKED", f"Permission status is '{creator.get('permission_status')}', not ACTIVE."
        if creator.get("commercial_use_status") != "ALLOWED":
            status = "REVIEW_REQUIRED"
            notes.append(f"Creator commercial-use status is {creator.get('commercial_use_status')}.")
        if creator.get("expiration_date"):
            exp = datetime.fromisoformat(creator["expiration_date"])
            if exp <= datetime.now(IST):
                return "BLOCKED", f"Creator permission expired on {creator['expiration_date']}."
        notes.append(f"Creator verified: {creator['name']}.")

    if game_name:
        with get_db(db_path) as conn:
            row = conn.execute("SELECT * FROM game_policies WHERE game_name = ?", (game_name,)).fetchone()
            if row:
                if row["commercial_policy"] == "FORBIDDEN":
                    return "BLOCKED", f"Game {game_name} publisher strictly forbids commercial derivative use."
                elif row["commercial_policy"] == "UNCLEAR":
                    status = "REVIEW_REQUIRED"
                    notes.append(f"Game {game_name} publisher policy is unclear; manual legal review required.")
                else:
                    notes.append(f"Game {game_name} policy verified.")
            else:
                status = "REVIEW_REQUIRED"
                notes.append(f"Game {game_name} has no policy record on file.")

    return status, " | ".join(notes) or "Standard source rights review passed."


# ==========================================
# BATCH CREATION & SOURCE IMPORT (FR-01)
# ==========================================

def create_batch(
    name: str,
    target_videos_per_day: int = 3,
    schedule_days: int = 3,
    daily_shorts: int = 2,
    daily_long: int = 1,
    language: str = "en",
    whisper_model: str = "small",
    timezone_str: str = "Asia/Kolkata",
    batch_id: str | None = None,
    db_path: Path = BATCH_DB
) -> dict[str, Any]:
    bid = batch_id or f"batch_{uuid.uuid4().hex[:8]}"
    batch_target = target_videos_per_day * schedule_days
    now = _now_ist_iso()
    with get_db(db_path) as conn:
        conn.execute("""
            INSERT INTO batches (id, name, status, target_videos_per_day, schedule_days, batch_target,
                                 daily_shorts, daily_long, timezone, language, whisper_model, created_at, updated_at)
            VALUES (?, ?, 'DRAFT', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (bid, name, target_videos_per_day, schedule_days, batch_target, daily_shorts, daily_long,
              timezone_str, language, whisper_model, now, now))
    log_audit("CREATE_BATCH", "batch", bid, batch_id=bid, details=f"Target: {batch_target} videos across {schedule_days} days", db_path=db_path)
    return get_batch(bid, db_path=db_path) or {}


def get_batch(batch_id: str, db_path: Path = BATCH_DB) -> dict[str, Any] | None:
    with get_db(db_path) as conn:
        row = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if not row:
            return None
        batch = dict(row)
        batch["sources"] = [dict(s) for s in conn.execute("SELECT * FROM sources WHERE batch_id = ?", (batch_id,)).fetchall()]
        batch["jobs"] = [dict(j) for j in conn.execute("SELECT * FROM jobs WHERE batch_id = ?", (batch_id,)).fetchall()]
        batch["clips"] = [dict(c) for c in conn.execute("SELECT * FROM clips WHERE batch_id = ?", (batch_id,)).fetchall()]
        batch["schedules"] = [dict(sc) for sc in conn.execute("SELECT * FROM schedules WHERE batch_id = ?", (batch_id,)).fetchall()]
        return batch


def list_batches(db_path: Path = BATCH_DB) -> list[dict[str, Any]]:
    with get_db(db_path) as conn:
        rows = conn.execute("SELECT * FROM batches ORDER BY created_at DESC").fetchall()
        result = []
        for r in rows:
            b = dict(r)
            b["source_count"] = conn.execute("SELECT COUNT(*) FROM sources WHERE batch_id = ?", (b["id"],)).fetchone()[0]
            b["clip_count"] = conn.execute("SELECT COUNT(*) FROM clips WHERE batch_id = ?", (b["id"],)).fetchone()[0]
            b["approved_count"] = conn.execute("SELECT COUNT(*) FROM clips WHERE batch_id = ? AND approval_status = 'APPROVED'", (b["id"],)).fetchone()[0]
            result.append(b)
        return result


def add_source_to_batch(
    batch_id: str,
    source_url: str,
    creator_id: str | None = None,
    game_name: str | None = None,
    title: str = "",
    file_path: str = "",
    source_id: str | None = None,
    db_path: Path = BATCH_DB
) -> dict[str, Any]:
    """Add a source to a batch with automatic creator and game policy rights assessment."""
    batch = get_batch(batch_id, db_path=db_path)
    if not batch:
        raise ValueError(f"Batch {batch_id} not found.")

    sid = source_id or f"source_{uuid.uuid4().hex[:8]}"
    now = _now_ist_iso()

    # Prevent accidental duplicate URL in the same batch
    with get_db(db_path) as conn:
        dup = conn.execute("SELECT id FROM sources WHERE batch_id = ? AND source_url = ?", (batch_id, source_url)).fetchone()
        if dup:
            raise ValueError(f"Source URL '{source_url}' is already in this batch.")

    rights_status, rights_notes = validate_source_rights(creator_id, game_name, db_path=db_path)

    with get_db(db_path) as conn:
        conn.execute("""
            INSERT INTO sources (id, batch_id, creator_id, source_url, file_path, title, game_name,
                                 rights_status, rights_notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (sid, batch_id, creator_id, source_url, file_path, title or source_url, game_name,
              rights_status, rights_notes, now))

        # Create persistent job queue entry for this source
        jid = f"job_{uuid.uuid4().hex[:8]}"
        conn.execute("""
            INSERT INTO jobs (id, batch_id, source_id, job_type, status, progress, stage_message,
                              retry_count, error_message, updated_at)
            VALUES (?, ?, ?, 'SOURCE_PROCESS', 'QUEUED', 0, 'Awaiting processing worker', 0, '', ?)
        """, (jid, batch_id, sid, now))

    log_audit("ADD_SOURCE", "source", sid, batch_id=batch_id,
              details=f"URL: {source_url} | Rights: {rights_status}", db_path=db_path)
    return {"id": sid, "batch_id": batch_id, "rights_status": rights_status, "rights_notes": rights_notes}


# ==========================================
# JOB STATE MACHINE (FR-14)
# ==========================================

def update_job_status(
    job_id: str,
    status: str,
    progress: int = 0,
    stage_message: str = "",
    error_message: str = "",
    db_path: Path = BATCH_DB
) -> None:
    if status not in JOB_STATES:
        raise ValueError(f"Invalid job state: '{status}'. Must be one of {JOB_STATES}")

    now = _now_ist_iso()
    with get_db(db_path) as conn:
        conn.execute("""
            UPDATE jobs
            SET status = ?, progress = ?, stage_message = ?, error_message = ?, updated_at = ?
            WHERE id = ?
        """, (status, progress, stage_message, error_message, now, job_id))

        if status == "COMPLETED":
            conn.execute("UPDATE jobs SET completed_at = ? WHERE id = ?", (now, job_id))
        elif status == "DOWNLOADING" and not conn.execute("SELECT started_at FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]:
            conn.execute("UPDATE jobs SET started_at = ? WHERE id = ?", (now, job_id))


# ==========================================
# 100% MANUAL APPROVAL GATE (FR-11)
# ==========================================

def set_clip_approval(
    clip_id: str,
    status: str,
    db_path: Path = BATCH_DB
) -> dict[str, Any]:
    """Manually approve or reject a clip. Unapproved clips cannot be scheduled or published."""
    if status not in ("APPROVED", "REJECTED", "PENDING"):
        raise ValueError("Status must be APPROVED, REJECTED, or PENDING")

    now = _now_ist_iso()
    with get_db(db_path) as conn:
        clip = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if not clip:
            raise ValueError(f"Clip {clip_id} not found.")

        if status == "APPROVED" and clip["rights_status"] == "BLOCKED":
            raise ValueError("Cannot approve a clip whose rights status is BLOCKED.")

        conn.execute("""
            UPDATE clips SET approval_status = ?, approved_at = ? WHERE id = ?
        """, (status, now if status == "APPROVED" else None, clip_id))

    log_audit("CLIP_APPROVAL", "clip", clip_id, batch_id=clip["batch_id"], details=f"Status: {status}", db_path=db_path)
    return {"id": clip_id, "approval_status": status, "approved_at": now if status == "APPROVED" else None}


# ==========================================
# BATCH SCHEDULER (FR-12: 3 Days x 3 Slots = 9 Target)
# ==========================================

def generate_batch_schedule_slots(
    batch_id: str,
    start_date_str: str | None = None,
    slot_hours: list[str] | None = None,
    db_path: Path = BATCH_DB
) -> list[dict[str, Any]]:
    """
    Generate schedule slots for a 3-day batch targeting 9 approved videos (2 Shorts + 1 Long per day).
    Only approved clips can be slotted. Unapproved videos will trigger a rejection.
    """
    batch = get_batch(batch_id, db_path=db_path)
    if not batch:
        raise ValueError(f"Batch {batch_id} not found.")

    with get_db(db_path) as conn:
        approved_clips = conn.execute("""
            SELECT * FROM clips WHERE batch_id = ? AND approval_status = 'APPROVED'
            ORDER BY hook_score DESC
        """, (batch_id,)).fetchall()

    if not approved_clips:
        raise ValueError("No approved clips available. All scheduled videos require 100% manual approval.")

    shorts = [dict(c) for c in approved_clips if c["format"] == "SHORT"]
    longs = [dict(c) for c in approved_clips if c["format"] == "LONG"]

    # Compute start date (default tomorrow in Asia/Kolkata)
    if start_date_str:
        start_date = datetime.fromisoformat(start_date_str).date()
    else:
        start_date = (datetime.now(IST) + timedelta(days=1)).date()

    slots_def = DEFAULT_SLOTS
    scheduled_items = []
    slot_counter = 1

    with get_db(db_path) as conn:
        # Clear any prior uncommitted pending schedule for this batch
        conn.execute("DELETE FROM schedules WHERE batch_id = ? AND status = 'PENDING'", (batch_id,))

        for day_offset in range(batch["schedule_days"]):
            day = start_date + timedelta(days=day_offset)

            for s_def in slots_def:
                target_format = s_def["format"]
                clip_to_schedule = None

                if target_format == "SHORT" and shorts:
                    clip_to_schedule = shorts.pop(0)
                elif target_format == "LONG" and longs:
                    clip_to_schedule = longs.pop(0)
                elif shorts:
                    clip_to_schedule = shorts.pop(0)
                elif longs:
                    clip_to_schedule = longs.pop(0)

                if not clip_to_schedule:
                    break

                slot_time_obj = datetime.strptime(s_def["time"], "%H:%M:%S").time()
                scheduled_dt = datetime.combine(day, slot_time_obj, tzinfo=IST)

                sched_id = f"sched_{uuid.uuid4().hex[:8]}"
                conn.execute("""
                    INSERT INTO schedules (id, batch_id, clip_id, slot_number, scheduled_at, slot_time,
                                           format, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING')
                """, (sched_id, batch_id, clip_to_schedule["id"], slot_counter,
                      scheduled_dt.isoformat(), s_def["label"], clip_to_schedule["format"]))

                scheduled_items.append({
                    "id": sched_id,
                    "slot_number": slot_counter,
                    "day": day.isoformat(),
                    "slot_time": s_def["label"],
                    "scheduled_at": scheduled_dt.isoformat(),
                    "clip_id": clip_to_schedule["id"],
                    "title": clip_to_schedule["title"],
                    "format": clip_to_schedule["format"]
                })
                slot_counter += 1

        conn.execute("UPDATE batches SET status = 'SCHEDULED', updated_at = ? WHERE id = ?",
                     (_now_ist_iso(), batch_id))

    log_audit("SCHEDULE_BATCH", "batch", batch_id, batch_id=batch_id,
              details=f"Scheduled {len(scheduled_items)} approved items across {batch['schedule_days']} days",
              db_path=db_path)
    return scheduled_items


# ==========================================
# BATCH COMPLETION REPORT (FR-15)
# ==========================================

def get_batch_report(batch_id: str, db_path: Path = BATCH_DB) -> dict[str, Any]:
    batch = get_batch(batch_id, db_path=db_path)
    if not batch:
        raise ValueError(f"Batch {batch_id} not found.")

    with get_db(db_path) as conn:
        sources_count = conn.execute("SELECT COUNT(*) FROM sources WHERE batch_id = ?", (batch_id,)).fetchone()[0]
        clips_count = conn.execute("SELECT COUNT(*) FROM clips WHERE batch_id = ?", (batch_id,)).fetchone()[0]
        shorts_count = conn.execute("SELECT COUNT(*) FROM clips WHERE batch_id = ? AND format = 'SHORT'", (batch_id,)).fetchone()[0]
        longs_count = conn.execute("SELECT COUNT(*) FROM clips WHERE batch_id = ? AND format = 'LONG'", (batch_id,)).fetchone()[0]
        approved_count = conn.execute("SELECT COUNT(*) FROM clips WHERE batch_id = ? AND approval_status = 'APPROVED'", (batch_id,)).fetchone()[0]
        blocked_count = conn.execute("SELECT COUNT(*) FROM sources WHERE batch_id = ? AND rights_status = 'BLOCKED'", (batch_id,)).fetchone()[0]
        review_count = conn.execute("SELECT COUNT(*) FROM clips WHERE batch_id = ? AND approval_status = 'PENDING'", (batch_id,)).fetchone()[0]
        scheduled_count = conn.execute("SELECT COUNT(*) FROM schedules WHERE batch_id = ?", (batch_id,)).fetchone()[0]
        failed_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE batch_id = ? AND status = 'FAILED'", (batch_id,)).fetchone()[0]

    return {
        "batch_id": batch_id,
        "batch_name": batch["name"],
        "status": batch["status"],
        "target_batch": batch["batch_target"],
        "sources_imported": sources_count,
        "candidate_clips_generated": clips_count,
        "shorts_completed": shorts_count,
        "long_videos_completed": longs_count,
        "videos_approved": approved_count,
        "videos_requiring_review": review_count,
        "sources_blocked_by_rights": blocked_count,
        "videos_scheduled": scheduled_count,
        "failed_jobs": failed_jobs,
        "ready_to_publish": approved_count >= 1 and scheduled_count >= 1
    }
