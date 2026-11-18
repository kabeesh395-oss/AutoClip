from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autoclip_app.production.models import (
    ContentItem,
    PlatformType,
    PrivacyStatus,
    PublishSchedule,
    PublishStatus,
)
from autoclip_app.production.storage import ProductionStorage
from autoclip_app.production.storage_cleaner import (
    StorageCleanupManager,
    StorageCleanupSettings,
    format_bytes,
    format_remaining_duration,
)


@pytest.fixture
def temp_env(tmp_path: Path):
    db_file = tmp_path / "test_production.sqlite3"
    storage = ProductionStorage(db_file)

    # Output directory for test rendered files
    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    # Mock YouTube verifier
    mock_status_map: dict[str, tuple[bool, str]] = {}

    def mock_yt_verifier(vid: str) -> tuple[bool, str]:
        return mock_status_map.get(vid, (True, "Mock Public"))

    cleaner = StorageCleanupManager(
        storage=storage,
        db_path=db_file,
        youtube_verifier=mock_yt_verifier,
    )
    # Monkeypatch ALLOWED_CLEANUP_ROOTS so temp outputs_dir is recognized as safe
    import autoclip_app.production.storage_cleaner as sc_mod
    sc_mod.ALLOWED_CLEANUP_ROOTS = (outputs_dir.resolve(),)

    yield {
        "storage": storage,
        "cleaner": cleaner,
        "outputs_dir": outputs_dir,
        "db_file": db_file,
        "mock_status_map": mock_status_map,
    }


def test_public_video_deleted_after_72_hours(temp_env):
    """Rule E: Public video uploaded > 72 hours ago is verified and safely deleted."""
    storage = temp_env["storage"]
    cleaner = temp_env["cleaner"]
    outputs_dir = temp_env["outputs_dir"]

    # Create dummy rendered video file
    dummy_video = outputs_dir / "rendered_clip_1.mp4"
    dummy_video.write_bytes(b"VIDEO_DATA_" * 1000)

    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    pub_time = now - timedelta(hours=73)  # 73 hours ago (> 72h)

    item = ContentItem(
        id="item_pub_1",
        source_clip_id="src_1",
        title="Epic Public Moment",
        video_path_vertical=str(dummy_video),
        created_at=pub_time.isoformat(),
    )
    storage.save_content_item(item)

    sched = PublishSchedule(
        id="sched_pub_1",
        item_id="item_pub_1",
        platform=PlatformType.YOUTUBE_SHORTS.value,
        scheduled_time_iso=pub_time.isoformat(),
        privacy_status=PrivacyStatus.PUBLIC.value,
        status=PublishStatus.PUBLISHED.value,
        external_id="yt_vid_123",
        external_url="https://youtube.com/shorts/yt_vid_123",
        published_at=pub_time.isoformat(),
        created_at=pub_time.isoformat(),
        updated_at=pub_time.isoformat(),
    )
    storage.create_schedule(sched)

    # Verify candidate report shows eligible
    report = cleaner.get_storage_report(now=now)
    assert report.eligible_for_deletion == 1
    assert report.candidates[0].eligible_for_deletion is True

    # Run cleanup
    res = cleaner.run_cleanup(now=now)
    assert res["deleted_count"] == 1
    assert not dummy_video.exists(), "File should be deleted after 72h public"

    # Verify history recorded in SQLite
    history = cleaner.list_history()
    assert len(history) == 1
    assert history[0].status == "DELETED"
    assert history[0].external_id == "yt_vid_123"


def test_public_video_retained_when_under_72_hours(temp_env):
    """Rule E: Public video uploaded < 72 hours ago is retained with active countdown."""
    storage = temp_env["storage"]
    cleaner = temp_env["cleaner"]
    outputs_dir = temp_env["outputs_dir"]

    dummy_video = outputs_dir / "rendered_recent.mp4"
    dummy_video.write_bytes(b"VIDEO_DATA_RECENT")

    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    pub_time = now - timedelta(hours=24)  # 24 hours ago (48h left)

    item = ContentItem(
        id="item_recent",
        source_clip_id="src_rec",
        title="Recent Public Moment",
        video_path_vertical=str(dummy_video),
        created_at=pub_time.isoformat(),
    )
    storage.save_content_item(item)

    sched = PublishSchedule(
        id="sched_rec",
        item_id="item_recent",
        platform=PlatformType.YOUTUBE_SHORTS.value,
        scheduled_time_iso=pub_time.isoformat(),
        privacy_status=PrivacyStatus.PUBLIC.value,
        status=PublishStatus.PUBLISHED.value,
        external_id="yt_recent_456",
        published_at=pub_time.isoformat(),
        created_at=pub_time.isoformat(),
        updated_at=pub_time.isoformat(),
    )
    storage.create_schedule(sched)

    # Check candidate countdown
    report = cleaner.get_storage_report(now=now)
    assert report.eligible_for_deletion == 0
    assert report.public_awaiting_cleanup == 1
    assert report.candidates[0].eligible_for_deletion is False
    assert "48h 0m remaining" in report.candidates[0].remaining_formatted

    # Run cleanup -> file must NOT be deleted
    res = cleaner.run_cleanup(now=now)
    assert res["deleted_count"] == 0
    assert dummy_video.exists(), "Video under 72h must be kept on disk"


def test_unlisted_and_private_videos_strictly_retained(temp_env):
    """Rule C: Videos uploaded as Unlisted or Private are never deleted."""
    storage = temp_env["storage"]
    cleaner = temp_env["cleaner"]
    outputs_dir = temp_env["outputs_dir"]

    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    old_time = now - timedelta(days=10)  # 10 days ago

    # 1. Unlisted Video
    unlisted_file = outputs_dir / "unlisted_video.mp4"
    unlisted_file.write_bytes(b"UNLISTED_DATA")

    item1 = ContentItem(id="item_unlisted", source_clip_id="s1", title="Unlisted", video_path_vertical=str(unlisted_file), created_at=old_time.isoformat())
    storage.save_content_item(item1)
    sched1 = PublishSchedule(
        id="sched_unlisted", item_id="item_unlisted", platform=PlatformType.YOUTUBE_SHORTS.value,
        scheduled_time_iso=old_time.isoformat(), privacy_status=PrivacyStatus.UNLISTED.value,
        status=PublishStatus.PUBLISHED.value, external_id="yt_unlisted_1", published_at=old_time.isoformat(),
        created_at=old_time.isoformat(), updated_at=old_time.isoformat()
    )
    storage.create_schedule(sched1)

    # 2. Private Video
    private_file = outputs_dir / "private_video.mp4"
    private_file.write_bytes(b"PRIVATE_DATA")

    item2 = ContentItem(id="item_private", source_clip_id="s2", title="Private", video_path_vertical=str(private_file), created_at=old_time.isoformat())
    storage.save_content_item(item2)
    sched2 = PublishSchedule(
        id="sched_private", item_id="item_private", platform=PlatformType.YOUTUBE_SHORTS.value,
        scheduled_time_iso=old_time.isoformat(), privacy_status=PrivacyStatus.PRIVATE.value,
        status=PublishStatus.PUBLISHED.value, external_id="yt_private_1", published_at=old_time.isoformat(),
        created_at=old_time.isoformat(), updated_at=old_time.isoformat()
    )
    storage.create_schedule(sched2)

    # Run cleanup
    res = cleaner.run_cleanup(now=now)
    assert res["deleted_count"] == 0
    assert unlisted_file.exists(), "Unlisted video must NEVER be deleted"
    assert private_file.exists(), "Private video must NEVER be deleted"


def test_rendered_not_uploaded_and_failed_uploads_retained(temp_env):
    """Rules A & B: Rendered but not uploaded or failed uploads are kept for retry."""
    storage = temp_env["storage"]
    cleaner = temp_env["cleaner"]
    outputs_dir = temp_env["outputs_dir"]

    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    old_time = now - timedelta(days=5)

    # Rendered only, no schedule
    not_uploaded_file = outputs_dir / "not_uploaded.mp4"
    not_uploaded_file.write_bytes(b"NOT_UPLOADED")
    item_nu = ContentItem(id="item_nu", source_clip_id="s_nu", title="Not Uploaded", video_path_vertical=str(not_uploaded_file), created_at=old_time.isoformat())
    storage.save_content_item(item_nu)

    # Failed upload
    failed_file = outputs_dir / "failed_upload.mp4"
    failed_file.write_bytes(b"FAILED_UPLOAD")
    item_fail = ContentItem(id="item_fail", source_clip_id="s_fail", title="Failed Upload", video_path_vertical=str(failed_file), created_at=old_time.isoformat())
    storage.save_content_item(item_fail)
    sched_fail = PublishSchedule(
        id="sched_fail", item_id="item_fail", platform=PlatformType.YOUTUBE_SHORTS.value,
        scheduled_time_iso=old_time.isoformat(), privacy_status=PrivacyStatus.PUBLIC.value,
        status=PublishStatus.FAILED.value, error_message="Network error",
        created_at=old_time.isoformat(), updated_at=old_time.isoformat()
    )
    storage.create_schedule(sched_fail)

    res = cleaner.run_cleanup(now=now)
    assert res["deleted_count"] == 0
    assert not_uploaded_file.exists(), "Not uploaded file must be kept"
    assert failed_file.exists(), "Failed upload file must be kept"


def test_scheduled_video_retained(temp_env):
    """Rule D: Scheduled videos are kept until actually published."""
    storage = temp_env["storage"]
    cleaner = temp_env["cleaner"]
    outputs_dir = temp_env["outputs_dir"]

    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    sched_file = outputs_dir / "scheduled_clip.mp4"
    sched_file.write_bytes(b"SCHEDULED_DATA")

    item = ContentItem(id="item_sched", source_clip_id="s_sc", title="Scheduled Clip", video_path_vertical=str(sched_file), created_at=now.isoformat())
    storage.save_content_item(item)
    sched = PublishSchedule(
        id="sched_future", item_id="item_sched", platform=PlatformType.YOUTUBE_SHORTS.value,
        scheduled_time_iso=(now + timedelta(days=2)).isoformat(), privacy_status=PrivacyStatus.PUBLIC.value,
        status=PublishStatus.SCHEDULED.value, created_at=now.isoformat(), updated_at=now.isoformat()
    )
    storage.create_schedule(sched)

    res = cleaner.run_cleanup(now=now)
    assert res["deleted_count"] == 0
    assert sched_file.exists(), "Scheduled video must not be deleted"


def test_youtube_verification_failure_prevents_deletion(temp_env):
    """Safety: If YouTube API reports video is private, removed or unreachable, retain file."""
    storage = temp_env["storage"]
    cleaner = temp_env["cleaner"]
    outputs_dir = temp_env["outputs_dir"]
    mock_status_map = temp_env["mock_status_map"]

    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    pub_time = now - timedelta(hours=80)

    dummy_video = outputs_dir / "rendered_offline_check.mp4"
    dummy_video.write_bytes(b"DATA")

    item = ContentItem(id="item_offline", source_clip_id="s_off", title="Offline Video", video_path_vertical=str(dummy_video), created_at=pub_time.isoformat())
    storage.save_content_item(item)
    sched = PublishSchedule(
        id="sched_off", item_id="item_offline", platform=PlatformType.YOUTUBE_SHORTS.value,
        scheduled_time_iso=pub_time.isoformat(), privacy_status=PrivacyStatus.PUBLIC.value,
        status=PublishStatus.PUBLISHED.value, external_id="yt_offline_fail", published_at=pub_time.isoformat(),
        created_at=pub_time.isoformat(), updated_at=pub_time.isoformat()
    )
    storage.create_schedule(sched)

    # Set mock verification to fail (e.g. YouTube returns 404 or video was deleted/privatized)
    mock_status_map["yt_offline_fail"] = (False, "Video is Private or Removed")

    res = cleaner.run_cleanup(now=now)
    assert res["deleted_count"] == 0
    assert dummy_video.exists(), "File must be retained when YouTube verification fails"

    history = cleaner.list_history()
    assert history[0].status == "SKIPPED"
    assert "YouTube verification failed" in history[0].reason


def test_path_traversal_and_unauthorized_path_blocked(temp_env):
    """Safety: Deletion outside allowed outputs folder is strictly blocked."""
    cleaner = temp_env["cleaner"]

    # Test path outside allowed root
    outside_file = Path("C:/Windows/notepad.exe")
    is_safe, err = cleaner.is_safe_file_path(outside_file)
    assert is_safe is False
    assert "outside designated" in err or "protected" in err

    # Test relative path traversal
    traversal_file = temp_env["outputs_dir"] / ".." / "something.txt"
    is_safe2, _ = cleaner.is_safe_file_path(traversal_file)
    assert is_safe2 is False


def test_settings_persistence_and_user_controls(temp_env):
    """User Controls: Settings survive and can switch between 48h and 72h."""
    cleaner = temp_env["cleaner"]

    # Initial default
    s1 = cleaner.get_settings()
    assert s1.auto_cleanup_enabled is True
    assert s1.retention_hours == 72

    # Switch to 48 hours
    cleaner.update_settings(retention_hours=48, auto_cleanup_enabled=False)
    s2 = cleaner.get_settings()
    assert s2.auto_cleanup_enabled is False
    assert s2.retention_hours == 48

    # Recreate cleaner instance pointing to same db to simulate app restart
    restarted_cleaner = StorageCleanupManager(db_path=temp_env["db_file"])
    s3 = restarted_cleaner.get_settings()
    assert s3.auto_cleanup_enabled is False
    assert s3.retention_hours == 48


def test_format_helpers():
    assert format_bytes(500) == "500 B"
    assert format_bytes(1024 * 1024 * 5) == "5.0 MB"
    assert "remaining" in format_remaining_duration(3600 * 5)
    assert "Ready to clean" in format_remaining_duration(0)


def test_storage_api_endpoints():
    """Test REST API endpoints for storage cleanup."""
    from fastapi.testclient import TestClient
    from autoclip_app.app import app

    client = TestClient(app)

    # 1. GET status
    resp = client.get("/api/production/storage/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_rendered_bytes" in data
    assert "total_recovered_bytes" in data
    assert "auto_cleanup_enabled" in data
    assert "retention_hours" in data
    assert "candidates" in data

    # 2. GET settings
    resp = client.get("/api/production/storage/settings")
    assert resp.status_code == 200
    settings = resp.json()
    assert settings["retention_hours"] in (48, 72)

    # 3. POST settings
    resp = client.post("/api/production/storage/settings", json={
        "auto_cleanup_enabled": True,
        "retention_hours": 72,
        "dry_run": False,
    })
    assert resp.status_code == 200
    assert resp.json()["settings"]["retention_hours"] == 72
    assert resp.json()["settings"]["auto_cleanup_enabled"] is True

    # 4. POST cleanup-now (dry run)
    resp = client.post("/api/production/storage/cleanup-now", json={"dry_run": True})
    assert resp.status_code == 200
    res = resp.json()
    assert "deleted_count" in res
    assert "skipped_count" in res

    # 5. GET history
    resp = client.get("/api/production/storage/history?limit=10")
    assert resp.status_code == 200
    assert "history" in resp.json()
