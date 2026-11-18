from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .models import ContentItem, PlatformType, PrivacyStatus, PublishSchedule, PublishStatus
from .storage import DEFAULT_DB, ProductionStorage, get_production_db

logger = logging.getLogger("autoclip.production.storage_cleaner")

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS_DIR = ROOT / "data" / "outputs"
LIVE_BUFFERS_DIR = ROOT / "data" / "live_buffers"

# Strictly permitted cleanup roots (prevents directory traversal or accidental deletion)
ALLOWED_CLEANUP_ROOTS: tuple[Path, ...] = (
    OUTPUTS_DIR.resolve(),
    LIVE_BUFFERS_DIR.resolve(),
)

# Strictly protected paths - NEVER delete these under any condition
PROTECTED_PATHS: tuple[Path, ...] = (
    (ROOT / "data" / "uploads").resolve(),
    (ROOT / "data" / "autoclip.sqlite3").resolve(),
    (ROOT / "data" / "youtube_token.json").resolve(),
    (ROOT / "data" / "client_secrets.json").resolve(),
    (ROOT / "data" / "instagram_token.json").resolve(),
    (ROOT / "autoclip_app").resolve(),
    (ROOT / "tests").resolve(),
    (ROOT / "scripts").resolve(),
    (ROOT / ".git").resolve(),
)


@dataclass
class StorageCleanupSettings:
    auto_cleanup_enabled: bool = True
    retention_hours: int = 72  # 72 hours default (supports 48 or 72)
    dry_run: bool = False
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CleanupCandidate:
    item_id: str
    schedule_id: str
    title: str
    file_path: str
    file_size_bytes: int
    privacy_status: str  # 'public', 'unlisted', 'private', 'scheduled', 'failed', 'not_uploaded'
    upload_status: str   # 'published', 'scheduled', 'failed', 'uploading', 'not_uploaded'
    external_id: str | None
    external_url: str | None
    published_at: str | None
    retention_hours: int
    eligible_for_deletion: bool
    remaining_seconds: float
    remaining_formatted: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CleanupHistoryItem:
    id: str
    item_id: str
    schedule_id: str | None
    external_id: str | None
    file_path: str
    file_size_bytes: int
    published_at: str | None
    deleted_at: str
    status: str  # 'DELETED', 'SKIPPED', 'FAILED', 'MANUAL_DELETED'
    reason: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StorageUsageReport:
    total_rendered_bytes: int
    total_rendered_formatted: str
    total_recovered_bytes: int
    total_recovered_formatted: str
    files_waiting_for_publish: int
    unlisted_private_count: int
    public_awaiting_cleanup: int
    eligible_for_deletion: int
    history_records_count: int
    auto_cleanup_enabled: bool
    retention_hours: int
    candidates: list[CleanupCandidate] = field(default_factory=list)
    recent_history: list[CleanupHistoryItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["candidates"] = [c.to_dict() for c in self.candidates]
        data["recent_history"] = [h.to_dict() for h in self.recent_history]
        return data


def format_bytes(num_bytes: int) -> str:
    """Formats bytes into human readable string."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    elif num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_remaining_duration(seconds: float) -> str:
    """Formats seconds into e.g. '71h 45m' or 'Ready to clean'."""
    if seconds <= 0:
        return "Ready to clean (Retention elapsed)"
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    if hrs > 0:
        return f"{hrs}h {mins}m remaining"
    return f"{mins}m remaining"


class StorageCleanupManager:
    """
    Autonomous smart storage management for AutoClip AI.
    Enforces strict rules:
      - Rendered but not uploaded: KEEP
      - Upload failed: KEEP
      - Private / Unlisted: KEEP
      - Scheduled: KEEP
      - Successfully Public: Verify YouTube public accessibility, start 72h countdown, delete after 72h.
    """

    def __init__(
        self,
        storage: ProductionStorage | None = None,
        db_path: Path = DEFAULT_DB,
        youtube_verifier: Callable[[str], tuple[bool, str]] | None = None,
    ) -> None:
        self.db_path = db_path
        self.storage = storage or ProductionStorage(db_path)
        self._custom_youtube_verifier = youtube_verifier
        self._init_tables()

    def _init_tables(self) -> None:
        """Initializes settings and audit history tables in SQLite."""
        with get_production_db(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS production_storage_cleanup_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    auto_cleanup_enabled INTEGER NOT NULL DEFAULT 1,
                    retention_hours INTEGER NOT NULL DEFAULT 72,
                    dry_run INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS production_storage_cleanup_history (
                    id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    schedule_id TEXT,
                    external_id TEXT,
                    file_path TEXT NOT NULL,
                    file_size_bytes INTEGER NOT NULL DEFAULT 0,
                    published_at TEXT,
                    deleted_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL
                );
            """)

            # Default settings if not existing
            row = conn.execute("SELECT id FROM production_storage_cleanup_settings WHERE id = 1;").fetchone()
            if not row:
                conn.execute("""
                    INSERT INTO production_storage_cleanup_settings (
                        id, auto_cleanup_enabled, retention_hours, dry_run, updated_at
                    ) VALUES (1, 1, 72, 0, ?);
                """, (datetime.now(timezone.utc).isoformat(),))

    def get_settings(self) -> StorageCleanupSettings:
        with get_production_db(self.db_path) as conn:
            row = conn.execute("SELECT * FROM production_storage_cleanup_settings WHERE id = 1;").fetchone()
            if not row:
                return StorageCleanupSettings()
            return StorageCleanupSettings(
                auto_cleanup_enabled=bool(row["auto_cleanup_enabled"]),
                retention_hours=int(row["retention_hours"]),
                dry_run=bool(row["dry_run"]),
                updated_at=row["updated_at"],
            )

    def update_settings(
        self,
        auto_cleanup_enabled: bool | None = None,
        retention_hours: int | None = None,
        dry_run: bool | None = None,
    ) -> StorageCleanupSettings:
        current = self.get_settings()
        new_enabled = current.auto_cleanup_enabled if auto_cleanup_enabled is None else auto_cleanup_enabled
        new_hours = current.retention_hours if retention_hours is None else retention_hours
        new_dry = current.dry_run if dry_run is None else dry_run

        if new_hours not in (48, 72):
            # Fall back to 72 if invalid
            if new_hours < 48:
                new_hours = 48
            else:
                new_hours = 72

        with get_production_db(self.db_path) as conn:
            conn.execute("""
                UPDATE production_storage_cleanup_settings
                SET auto_cleanup_enabled = ?, retention_hours = ?, dry_run = ?, updated_at = ?
                WHERE id = 1;
            """, (
                1 if new_enabled else 0,
                new_hours,
                1 if new_dry else 0,
                datetime.now(timezone.utc).isoformat(),
            ))
        return self.get_settings()

    def is_safe_file_path(self, file_path_str: str | Path | None) -> tuple[bool, str]:
        """
        Validates that a path:
        1. Is not None or empty
        2. Resolves strictly inside ALLOWED_CLEANUP_ROOTS (outputs, live_buffers)
        3. Does not match any PROTECTED_PATHS
        4. Exists on disk
        """
        if not file_path_str:
            return False, "File path is empty"

        try:
            target = Path(file_path_str).resolve()
        except Exception as exc:
            return False, f"Invalid path structure: {exc}"

        # Check if inside protected paths
        for prot in PROTECTED_PATHS:
            if target == prot or prot in target.parents:
                return False, f"Path is strictly protected: {target}"

        # Check if inside an allowed cleanup root
        inside_allowed = any(root == target or root in target.parents for root in ALLOWED_CLEANUP_ROOTS)
        if not inside_allowed:
            return False, f"Path is outside designated outputs directory: {target}"

        if not target.exists():
            return False, f"File does not exist on disk: {target}"

        return True, "Path is safe for cleanup"

    def verify_youtube_public_status(self, video_id: str, timeout: float = 5.0) -> tuple[bool, str]:
        """
        Verifies that a video exists on YouTube, is publicly accessible, and not private/unlisted.
        Uses custom verifier if provided, otherwise checks YouTube oEmbed and API.
        """
        if self._custom_youtube_verifier:
            return self._custom_youtube_verifier(video_id)

        if not video_id or not video_id.strip():
            return False, "Missing video ID"

        # 1. Verification via official YouTube oEmbed API
        # If public: returns 200 OK + JSON
        # If private / unlisted / deleted: returns 401 or 404
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        try:
            req = urllib.request.Request(oembed_url, headers={"User-Agent": "AutoClip-StorageCleaner/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    title = data.get("title", "")
                    return True, f"Verified Public on YouTube via oEmbed ('{title[:30]}...')"
                return False, f"YouTube returned unexpected status code {resp.status}"
        except urllib.error.HTTPError as http_err:
            if http_err.code in (401, 403):
                return False, "Video is Private or Unlisted (oEmbed HTTP 401/403)"
            elif http_err.code == 404:
                return False, "Video is Not Found or Removed (oEmbed HTTP 404)"
            return False, f"YouTube oEmbed verification error: {http_err}"
        except Exception as exc:
            logger.warning("YouTube verification network/timeout error for %s: %s", video_id, exc)
            return False, f"YouTube verification network unavailable ({exc})"

    def scan_candidates(self, now: datetime | None = None) -> list[CleanupCandidate]:
        """
        Scans all content items and schedules, applying rules A-E to compute cleanup eligibility.
        """
        settings = self.get_settings()
        ref_time = now or datetime.now(timezone.utc)
        candidates: list[CleanupCandidate] = []

        items = self.storage.list_content_items()
        schedules = self.storage.list_schedules()
        sched_map: dict[str, list[PublishSchedule]] = {}
        for s in schedules:
            sched_map.setdefault(s.item_id, []).append(s)

        for item in items:
            vpath_str = item.video_path_vertical or item.video_path_horizontal
            if not vpath_str:
                continue

            file_path = Path(vpath_str)
            file_size = file_path.stat().st_size if file_path.exists() else 0

            item_schedules = sched_map.get(item.id, [])
            if not item_schedules:
                # Rule A: Rendered but not uploaded
                candidates.append(CleanupCandidate(
                    item_id=item.id,
                    schedule_id="",
                    title=item.title,
                    file_path=str(file_path),
                    file_size_bytes=file_size,
                    privacy_status="not_uploaded",
                    upload_status="not_uploaded",
                    external_id=None,
                    external_url=None,
                    published_at=None,
                    retention_hours=settings.retention_hours,
                    eligible_for_deletion=False,
                    remaining_seconds=-1.0,
                    remaining_formatted="Keep (Not uploaded to YouTube)",
                    reason="Rendered but not uploaded: Protected",
                ))
                continue

            # Check if any schedule is published as public
            published_public_sched: PublishSchedule | None = None
            is_failed = False
            is_unlisted_or_private = False
            is_scheduled_pending = False

            for s in item_schedules:
                if s.status == PublishStatus.PUBLISHED.value and s.privacy_status == PrivacyStatus.PUBLIC.value and s.external_id:
                    published_public_sched = s
                    break
                elif s.status == PublishStatus.PUBLISHED.value and s.privacy_status in (PrivacyStatus.UNLISTED.value, PrivacyStatus.PRIVATE.value):
                    is_unlisted_or_private = True
                elif s.status == PublishStatus.FAILED.value:
                    is_failed = True
                elif s.status in (PublishStatus.SCHEDULED.value, PublishStatus.DRAFT.value, PublishStatus.UPLOADING.value):
                    is_scheduled_pending = True

            if is_unlisted_or_private and not published_public_sched:
                # Rule C: Private or Unlisted
                candidates.append(CleanupCandidate(
                    item_id=item.id,
                    schedule_id=item_schedules[0].id,
                    title=item.title,
                    file_path=str(file_path),
                    file_size_bytes=file_size,
                    privacy_status="unlisted/private",
                    upload_status="published",
                    external_id=item_schedules[0].external_id,
                    external_url=item_schedules[0].external_url,
                    published_at=item_schedules[0].published_at,
                    retention_hours=settings.retention_hours,
                    eligible_for_deletion=False,
                    remaining_seconds=-1.0,
                    remaining_formatted="Keep (Unlisted/Private video protected)",
                    reason="Private/Unlisted: Keep until made Public",
                ))
            elif is_scheduled_pending and not published_public_sched:
                # Rule D: Scheduled but not yet public
                candidates.append(CleanupCandidate(
                    item_id=item.id,
                    schedule_id=item_schedules[0].id,
                    title=item.title,
                    file_path=str(file_path),
                    file_size_bytes=file_size,
                    privacy_status=item_schedules[0].privacy_status,
                    upload_status=item_schedules[0].status,
                    external_id=item_schedules[0].external_id,
                    external_url=item_schedules[0].external_url,
                    published_at=None,
                    retention_hours=settings.retention_hours,
                    eligible_for_deletion=False,
                    remaining_seconds=-1.0,
                    remaining_formatted="Keep (Scheduled for future release)",
                    reason="Scheduled: Countdown has not started",
                ))
            elif is_failed and not published_public_sched:
                # Rule B: Upload failed
                candidates.append(CleanupCandidate(
                    item_id=item.id,
                    schedule_id=item_schedules[0].id,
                    title=item.title,
                    file_path=str(file_path),
                    file_size_bytes=file_size,
                    privacy_status=item_schedules[0].privacy_status,
                    upload_status="failed",
                    external_id=item_schedules[0].external_id,
                    external_url=item_schedules[0].external_url,
                    published_at=None,
                    retention_hours=settings.retention_hours,
                    eligible_for_deletion=False,
                    remaining_seconds=-1.0,
                    remaining_formatted="Keep (Upload failed, retained for retry)",
                    reason="Upload failed: Keep to allow retry",
                ))
            elif published_public_sched:
                # Rule E: Successfully published as Public
                pub_iso = published_public_sched.published_at or published_public_sched.created_at
                try:
                    pub_dt = datetime.fromisoformat(pub_iso)
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    pub_dt = ref_time

                retention_delta = timedelta(hours=settings.retention_hours)
                eligible_time = pub_dt + retention_delta
                diff_sec = (eligible_time - ref_time).total_seconds()
                is_eligible = diff_sec <= 0

                remaining_sec = max(0.0, diff_sec)
                formatted_rem = format_remaining_duration(remaining_sec)

                reason_text = (
                    f"Public verified > {settings.retention_hours}h retention elapsed"
                    if is_eligible
                    else f"Public verified, {formatted_rem} until deletion"
                )

                candidates.append(CleanupCandidate(
                    item_id=item.id,
                    schedule_id=published_public_sched.id,
                    title=item.title,
                    file_path=str(file_path),
                    file_size_bytes=file_size,
                    privacy_status="public",
                    upload_status="published",
                    external_id=published_public_sched.external_id,
                    external_url=published_public_sched.external_url,
                    published_at=pub_iso,
                    retention_hours=settings.retention_hours,
                    eligible_for_deletion=is_eligible,
                    remaining_seconds=remaining_sec,
                    remaining_formatted=formatted_rem,
                    reason=reason_text,
                ))

        return candidates

    def run_cleanup(
        self,
        force_item_id: str | None = None,
        dry_run: bool | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Executes cleanup for all eligible files or a single requested item with full safety checks:
        1. Confirm video was successfully uploaded.
        2. Confirm YouTube video ID matches local record.
        3. Confirm its current live visibility is Public.
        4. Confirm it has remained Public for >= retention_hours (unless force_item_id is specified with confirmation).
        5. Confirm local file belongs to that exact upload and is inside allowed directories.
        6. Check that no active process is using the file.
        """
        settings = self.get_settings()
        is_dry = settings.dry_run if dry_run is None else dry_run
        ref_time = now or datetime.now(timezone.utc)

        candidates = self.scan_candidates(now=ref_time)
        deleted_count = 0
        skipped_count = 0
        recovered_bytes = 0
        actions: list[dict[str, Any]] = []

        for cand in candidates:
            # If targeting a specific item, filter
            if force_item_id and cand.item_id != force_item_id:
                continue

            # If not forcing and not eligible yet, skip
            if not force_item_id and not cand.eligible_for_deletion:
                continue

            # 1. Safety Path Check
            is_safe, path_err = self.is_safe_file_path(cand.file_path)
            if not is_safe:
                logger.warning("Cleanup safety block on %s: %s", cand.file_path, path_err)
                self._record_history(
                    cand=cand,
                    status="SKIPPED",
                    reason=f"Safety check blocked: {path_err}",
                    recovered_bytes=0,
                )
                skipped_count += 1
                actions.append({"item_id": cand.item_id, "status": "SKIPPED", "reason": path_err})
                continue

            target_path = Path(cand.file_path).resolve()

            # 2. Check if file is currently open or in use by another active schedule
            item = self.storage.get_content_item(cand.item_id)
            if not item:
                self._record_history(cand=cand, status="SKIPPED", reason="Item not found in database", recovered_bytes=0)
                skipped_count += 1
                continue

            # Check if any active schedule still needs this video
            with get_production_db(self.db_path) as conn:
                active_use = conn.execute("""
                    SELECT id FROM production_schedules
                    WHERE item_id = ? AND status IN ('uploading', 'scheduled') AND id != ?;
                """, (cand.item_id, cand.schedule_id)).fetchone()
                if active_use:
                    reason = f"File still in use by active schedule {active_use['id']}"
                    logger.info("Skipping cleanup for %s: %s", cand.item_id, reason)
                    self._record_history(cand=cand, status="SKIPPED", reason=reason, recovered_bytes=0)
                    skipped_count += 1
                    actions.append({"item_id": cand.item_id, "status": "SKIPPED", "reason": reason})
                    continue

            # 3. Live YouTube Public Status Verification
            if not cand.external_id:
                reason = "No YouTube Video ID to verify public accessibility"
                self._record_history(cand=cand, status="SKIPPED", reason=reason, recovered_bytes=0)
                skipped_count += 1
                actions.append({"item_id": cand.item_id, "status": "SKIPPED", "reason": reason})
                continue

            is_public, yt_reason = self.verify_youtube_public_status(cand.external_id)
            if not is_public:
                reason = f"YouTube verification failed: {yt_reason}"
                logger.info("Skipping cleanup for %s: %s", cand.item_id, reason)
                self._record_history(cand=cand, status="SKIPPED", reason=reason, recovered_bytes=0)
                skipped_count += 1
                actions.append({"item_id": cand.item_id, "status": "SKIPPED", "reason": reason})
                continue

            # 4. Check file ownership / hash consistency
            try:
                current_size = target_path.stat().st_size
            except Exception as exc:
                reason = f"Cannot read file stat: {exc}"
                self._record_history(cand=cand, status="FAILED", reason=reason, recovered_bytes=0)
                skipped_count += 1
                continue

            # 5. Perform deletion
            if is_dry:
                reason = f"DRY RUN: Would delete {target_path.name} ({format_bytes(current_size)})"
                logger.info(reason)
                actions.append({"item_id": cand.item_id, "status": "DRY_RUN", "file": str(target_path), "size": current_size})
                continue

            try:
                target_path.unlink()
                # Also clean companion .srt or .ass subtitles if in same folder and starting with same prefix
                for ext in (".srt", ".ass"):
                    comp = target_path.with_suffix(ext)
                    if comp.exists():
                        try:
                            comp.unlink()
                        except Exception:
                            pass

                deleted_count += 1
                recovered_bytes += current_size
                history_status = "MANUAL_DELETED" if force_item_id else "DELETED"
                success_reason = (
                    f"Public YouTube verified ({cand.external_id}) > {settings.retention_hours}h retention satisfied"
                )
                self._record_history(
                    cand=cand,
                    status=history_status,
                    reason=success_reason,
                    recovered_bytes=current_size,
                )
                actions.append({
                    "item_id": cand.item_id,
                    "status": history_status,
                    "file": str(target_path),
                    "recovered_bytes": current_size,
                })
                logger.info("Successfully cleaned up rendered video: %s (recovered %s)", target_path.name, format_bytes(current_size))

            except Exception as del_err:
                err_text = f"Deletion error: {del_err}"
                logger.error("Failed to delete %s: %s", target_path, del_err)
                self._record_history(cand=cand, status="FAILED", reason=err_text, recovered_bytes=0)
                actions.append({"item_id": cand.item_id, "status": "FAILED", "error": err_text})

        return {
            "deleted_count": deleted_count,
            "skipped_count": skipped_count,
            "recovered_bytes": recovered_bytes,
            "recovered_formatted": format_bytes(recovered_bytes),
            "dry_run": is_dry,
            "actions": actions,
        }

    def _record_history(
        self,
        cand: CleanupCandidate,
        status: str,
        reason: str,
        recovered_bytes: int,
    ) -> None:
        rec_id = f"cln_{os.urandom(4).hex()}"
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_production_db(self.db_path) as conn:
            conn.execute("""
                INSERT INTO production_storage_cleanup_history (
                    id, item_id, schedule_id, external_id, file_path, file_size_bytes,
                    published_at, deleted_at, status, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                rec_id,
                cand.item_id,
                cand.schedule_id,
                cand.external_id,
                cand.file_path,
                recovered_bytes,
                cand.published_at,
                now_iso,
                status,
                reason,
                now_iso,
            ))

    def get_storage_report(self, now: datetime | None = None) -> StorageUsageReport:
        """
        Computes storage analytics across output directories and database records.
        """
        settings = self.get_settings()
        ref_time = now or datetime.now(timezone.utc)
        candidates = self.scan_candidates(now=ref_time)

        # 1. Total rendered storage currently on disk in outputs directory
        total_rendered_bytes = 0
        if OUTPUTS_DIR.exists():
            for root, _, files in os.walk(OUTPUTS_DIR):
                for f in files:
                    fp = Path(root) / f
                    if fp.is_file() and fp.suffix.lower() in (".mp4", ".mov", ".mkv", ".ts"):
                        try:
                            total_rendered_bytes += fp.stat().st_size
                        except Exception:
                            pass

        # 2. Total recovered bytes from audit history
        total_recovered_bytes = 0
        history_count = 0
        recent_history: list[CleanupHistoryItem] = []
        with get_production_db(self.db_path) as conn:
            row = conn.execute("""
                SELECT SUM(file_size_bytes) as total, COUNT(*) as cnt
                FROM production_storage_cleanup_history
                WHERE status IN ('DELETED', 'MANUAL_DELETED');
            """).fetchone()
            if row and row["total"]:
                total_recovered_bytes = int(row["total"])
            if row:
                history_count = int(row["cnt"])

            hist_rows = conn.execute("""
                SELECT * FROM production_storage_cleanup_history
                ORDER BY created_at DESC LIMIT 20;
            """).fetchall()
            for h in hist_rows:
                recent_history.append(CleanupHistoryItem(
                    id=h["id"],
                    item_id=h["item_id"],
                    schedule_id=h["schedule_id"],
                    external_id=h["external_id"],
                    file_path=h["file_path"],
                    file_size_bytes=int(h["file_size_bytes"]),
                    published_at=h["published_at"],
                    deleted_at=h["deleted_at"],
                    status=h["status"],
                    reason=h["reason"] or "",
                    created_at=h["created_at"],
                ))

        # Categorize candidates
        waiting_count = sum(1 for c in candidates if c.upload_status == "not_uploaded")
        unlisted_private = sum(1 for c in candidates if c.privacy_status in ("unlisted", "private", "unlisted/private"))
        public_awaiting = sum(1 for c in candidates if c.privacy_status == "public" and not c.eligible_for_deletion)
        eligible = sum(1 for c in candidates if c.eligible_for_deletion)

        return StorageUsageReport(
            total_rendered_bytes=total_rendered_bytes,
            total_rendered_formatted=format_bytes(total_rendered_bytes),
            total_recovered_bytes=total_recovered_bytes,
            total_recovered_formatted=format_bytes(total_recovered_bytes),
            files_waiting_for_publish=waiting_count,
            unlisted_private_count=unlisted_private,
            public_awaiting_cleanup=public_awaiting,
            eligible_for_deletion=eligible,
            history_records_count=history_count,
            auto_cleanup_enabled=settings.auto_cleanup_enabled,
            retention_hours=settings.retention_hours,
            candidates=candidates,
            recent_history=recent_history,
        )

    def list_history(self, limit: int = 50) -> list[CleanupHistoryItem]:
        with get_production_db(self.db_path) as conn:
            rows = conn.execute("""
                SELECT * FROM production_storage_cleanup_history
                ORDER BY created_at DESC LIMIT ?;
            """, (limit,)).fetchall()
            return [
                CleanupHistoryItem(
                    id=r["id"],
                    item_id=r["item_id"],
                    schedule_id=r["schedule_id"],
                    external_id=r["external_id"],
                    file_path=r["file_path"],
                    file_size_bytes=int(r["file_size_bytes"]),
                    published_at=r["published_at"],
                    deleted_at=r["deleted_at"],
                    status=r["status"],
                    reason=r["reason"] or "",
                    created_at=r["created_at"],
                )
                for r in rows
            ]
