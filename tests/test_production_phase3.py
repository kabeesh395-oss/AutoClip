from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoclip_app.app import app
from autoclip_app.production.analytics import AnalyticsEngine
from autoclip_app.production.calendar import CalendarManager
from autoclip_app.production.content_producer import ContentProducer, SubtitleTimingValidator, compute_file_sha256
from autoclip_app.production.models import (
    ContentItem,
    PlatformAnalytics,
    PlatformType,
    PrivacyStatus,
    ProductionRules,
    PublishSchedule,
    PublishStatus,
)
from autoclip_app.production.multi_publisher import (
    DuplicateUploadError,
    InstagramReelsPublisher,
    MultiPublisherRouter,
    PublisherError,
    QuotaExceededError,
    RightsConfirmationRequiredError,
    YouTubePublisher,
)
from autoclip_app.production.service import ProductionService
from autoclip_app.production.storage import ProductionStorage, init_production_tables


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_production.sqlite3"
    init_production_tables(db_file)
    return db_file


@pytest.fixture
def storage(temp_db: Path) -> ProductionStorage:
    return ProductionStorage(temp_db)


@pytest.fixture
def sample_video(tmp_path: Path) -> Path:
    p = tmp_path / "test_clip.mp4"
    p.write_bytes(b"dummy video data for phase 3 testing" * 100)
    return p


# -----------------------------------------------------------------------------
# 1. Models & Storage Tests
# -----------------------------------------------------------------------------

def test_models_serialization():
    item = ContentItem(
        id="item_001",
        title="Test Title",
        title_options=["Hook 1", "Hook 2", "Hook 3"],
        description="Test Description",
        hashtags=["#shorts", "#viral"],
        duration_seconds=45.0,
    )
    d = item.to_dict()
    assert d["id"] == "item_001"
    assert len(d["title_options"]) == 3
    assert d["duration_seconds"] == 45.0

    sched = PublishSchedule(
        id="sched_001",
        item_id="item_001",
        platform=PlatformType.YOUTUBE_SHORTS.value,
        rights_confirmed=True,
    )
    sd = sched.to_dict()
    assert sd["id"] == "sched_001"
    assert sd["rights_confirmed"] is True


def test_storage_crud(storage: ProductionStorage, sample_video: Path):
    h = compute_file_sha256(sample_video)
    item = ContentItem(
        id="item_test",
        title="Epic Stream Moment",
        title_options=["Option A", "Option B", "Option C"],
        video_path_vertical=str(sample_video),
        sha256_hash=h,
    )
    storage.save_content_item(item)

    loaded = storage.get_content_item("item_test")
    assert loaded is not None
    assert loaded.title == "Epic Stream Moment"
    assert loaded.sha256_hash == h
    assert len(loaded.title_options) == 3

    sched = PublishSchedule(
        id="sched_test",
        item_id="item_test",
        platform=PlatformType.YOUTUBE_SHORTS.value,
        scheduled_time_iso="2026-11-20T12:00:00Z",
        rights_confirmed=True,
    )
    storage.create_schedule(sched)

    loaded_s = storage.get_schedule("sched_test")
    assert loaded_s is not None
    assert loaded_s.status == PublishStatus.SCHEDULED.value

    # Test duplicate hash check
    assert storage.is_hash_published(h, PlatformType.YOUTUBE_SHORTS.value) is True
    assert storage.is_hash_published("unknown_hash", PlatformType.YOUTUBE_SHORTS.value) is False

    # Test rescheduling
    storage.reschedule("sched_test", "2026-11-21T16:00:00Z")
    assert storage.get_schedule("sched_test").scheduled_time_iso == "2026-11-21T16:00:00Z"

    # Test cancelling
    storage.cancel_schedule("sched_test")
    assert storage.get_schedule("sched_test").status == PublishStatus.CANCELLED.value

    # Test retry
    storage.retry_schedule("sched_test")
    assert storage.get_schedule("sched_test").status == PublishStatus.SCHEDULED.value


def test_storage_rules(storage: ProductionStorage):
    rules = storage.get_rules()
    assert rules.daily_limit_shorts == 3
    assert rules.require_rights_confirmation is True
    assert rules.full_auto_enabled is False

    rules.daily_limit_shorts = 5
    rules.preferred_slots = ["10:00", "15:00", "19:00"]
    storage.save_rules(rules)

    updated = storage.get_rules()
    assert updated.daily_limit_shorts == 5
    assert updated.preferred_slots == ["10:00", "15:00", "19:00"]


# -----------------------------------------------------------------------------
# 2. Content Producer & Subtitle Timing Validator
# -----------------------------------------------------------------------------

def test_subtitle_validator_valid():
    valid_srt = """1
00:00:01,000 --> 00:00:03,000
Hello world!

2
00:00:03,200 --> 00:00:05,500
This is AutoClip AI.
"""
    is_valid, errors = SubtitleTimingValidator.validate_srt(valid_srt, clip_duration=10.0)
    assert is_valid is True
    assert len(errors) == 0


def test_subtitle_validator_overlapping_and_fast():
    bad_srt = """1
00:00:01,000 --> 00:00:04,000
This is a very long sentence that has way too many characters for one second of screen time to test CPS warning!

2
00:00:03,000 --> 00:00:02,000
Inverted timestamp and overlap!
"""
    is_valid, errors = SubtitleTimingValidator.validate_srt(bad_srt, clip_duration=5.0)
    assert is_valid is False
    assert len(errors) >= 2


def test_content_producer_title_generation():
    producer = ContentProducer(ollama_url="http://127.0.0.1:99999")  # deliberately offline to test heuristic
    res = producer.generate_titles_and_descriptions(
        transcript="I can't believe this happened live on stream right now!",
        channel_name="KaiCenat",
        stream_title="Elden Ring Marathon",
        keywords=["VICTORY", "INSANE"],
    )
    assert "title" in res
    assert len(res["title_options"]) == 3
    assert "#shorts" in res["hashtags"]
    assert "#reels" in res["hashtags"]
    assert any("KaiCenat" in opt for opt in res["title_options"])


# -----------------------------------------------------------------------------
# 3. Multi-Publisher & Safety Gates
# -----------------------------------------------------------------------------

def test_rights_confirmation_gate(storage: ProductionStorage, sample_video: Path):
    router = MultiPublisherRouter(storage)
    item = ContentItem(
        id="item_unconfirmed",
        title="Unconfirmed Rights Item",
        video_path_vertical=str(sample_video),
    )
    storage.save_content_item(item)

    sched = PublishSchedule(
        id="sched_unconfirmed",
        item_id="item_unconfirmed",
        platform=PlatformType.YOUTUBE_SHORTS.value,
        rights_confirmed=False,  # Unconfirmed!
    )
    storage.create_schedule(sched)

    with pytest.raises(RightsConfirmationRequiredError):
        router.publish_schedule("sched_unconfirmed")

    updated = storage.get_schedule("sched_unconfirmed")
    assert updated.status == PublishStatus.FAILED.value
    assert "Rights & Ownership confirmation required" in updated.error_message


def test_duplicate_upload_prevention(storage: ProductionStorage, sample_video: Path):
    h = compute_file_sha256(sample_video)
    router = MultiPublisherRouter(storage)

    # First item: already published
    item1 = ContentItem(id="item1", video_path_vertical=str(sample_video), sha256_hash=h)
    storage.save_content_item(item1)
    sched1 = PublishSchedule(id="s1", item_id="item1", platform=PlatformType.YOUTUBE_SHORTS.value, status="published", rights_confirmed=True)
    storage.create_schedule(sched1)

    # Second item with same hash
    item2 = ContentItem(id="item2", video_path_vertical=str(sample_video), sha256_hash=h)
    storage.save_content_item(item2)
    sched2 = PublishSchedule(id="s2", item_id="item2", platform=PlatformType.YOUTUBE_SHORTS.value, status="scheduled", rights_confirmed=True)
    storage.create_schedule(sched2)

    with pytest.raises(DuplicateUploadError):
        router.publish_schedule("s2")

    assert storage.get_schedule("s2").status == PublishStatus.FAILED.value


def test_youtube_publisher_mock(storage: ProductionStorage, sample_video: Path):
    router = MultiPublisherRouter(storage)
    item = ContentItem(id="yt_item", title="YT Viral Short", video_path_vertical=str(sample_video))
    storage.save_content_item(item)

    sched = PublishSchedule(
        id="sched_yt",
        item_id="yt_item",
        platform=PlatformType.YOUTUBE_SHORTS.value,
        rights_confirmed=True,
    )
    storage.create_schedule(sched)

    with patch("autoclip_app.youtube.youtube_status", return_value={"connected": True, "channel_title": "Test Channel"}), \
         patch("autoclip_app.youtube.upload_public_video", return_value={"video_id": "yt_12345", "url": "https://youtu.be/12345"}):
        res = router.publish_schedule("sched_yt")
        assert res["external_id"] == "yt_12345"
        assert res["status"] == "published"

    updated = storage.get_schedule("sched_yt")
    assert updated.status == PublishStatus.PUBLISHED.value
    assert updated.external_id == "yt_12345"


def test_instagram_publisher_status():
    pub = InstagramReelsPublisher()
    # When no token file or env vars exist
    with patch.object(pub, "get_credentials", return_value={}):
        stat = pub.check_status()
        assert stat["connected"] is False
        assert "not connected" in stat["message"]


# -----------------------------------------------------------------------------
# 4. Calendar Manager & Slot Suggestions
# -----------------------------------------------------------------------------

def test_calendar_slot_suggestion(storage: ProductionStorage):
    mgr = CalendarManager(storage)
    slot = mgr.suggest_next_slot("youtube_shorts")
    assert slot.endswith("Z")
    assert "T" in slot


def test_calendar_view(storage: ProductionStorage, sample_video: Path):
    mgr = CalendarManager(storage)
    item = ContentItem(id="cal_item", title="Calendar Clip", video_path_vertical=str(sample_video))
    storage.save_content_item(item)

    sched = mgr.schedule_item(item_id="cal_item", platform="youtube_shorts", scheduled_time_iso="2026-11-25T16:00:00Z")
    assert sched.id.startswith("sched_")

    view = mgr.get_calendar_view()
    assert view["summary"]["total_items"] >= 1
    assert "2026-11-25" in view["days"]


# -----------------------------------------------------------------------------
# 5. Analytics & Feedback Learning
# -----------------------------------------------------------------------------

def test_analytics_sync_and_feedback(storage: ProductionStorage):
    engine = AnalyticsEngine(storage)
    item = ContentItem(id="item_analytics", source_clip_id="clip_001", title="Viral Clip")
    storage.save_content_item(item)

    sched = PublishSchedule(
        id="sched_pub",
        item_id="item_analytics",
        platform=PlatformType.YOUTUBE_SHORTS.value,
        external_id="yt_vid_999",
        status="published",
    )
    storage.create_schedule(sched)

    mock_yt = {
        "yt_vid_999": {
            "views": 2500,
            "likes": 250,
            "comments": 45,
            "shares": 10,
            "raw": {},
        }
    }

    with patch.object(engine, "sync_youtube_metrics", return_value=mock_yt), \
         patch("autoclip_app.trend_hunter.learning.FeedbackLearner.record_feedback") as mock_feedback:
        updated = engine.sync_all_analytics()
        assert len(updated) == 1
        rec = updated[0]
        assert rec.views == 2500
        assert rec.likes == 250
        assert rec.engagement_rate > 10.0  # (250+45+10)/2500 * 100 = 12.2%
        assert mock_feedback.called


# -----------------------------------------------------------------------------
# 6. FastAPI Endpoints Integration
# -----------------------------------------------------------------------------

@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_api_production_status(client: TestClient):
    resp = client.get("/api/production/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "platforms" in data
    assert "rules" in data
    assert "summary" in data


def test_api_production_rules(client: TestClient):
    resp = client.get("/api/production/rules")
    assert resp.status_code == 200
    rules = resp.json()
    assert "daily_limit_shorts" in rules

    # Update rules
    resp2 = client.post("/api/production/rules", json={
        "daily_limit_shorts": 4,
        "preferred_slots": ["11:00", "15:00", "20:00"],
    })
    assert resp2.status_code == 200
    assert resp2.json()["rules"]["daily_limit_shorts"] == 4


def test_api_production_calendar(client: TestClient):
    resp = client.get("/api/production/calendar")
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data
    assert "days" in data


def test_api_validate_subtitles(client: TestClient):
    srt = "1\n00:00:01,000 --> 00:00:03,000\nValid Subtitle\n"
    resp = client.post("/api/production/validate-subtitles", json={"srt_text": srt, "duration": 5.0})
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


def test_api_production_schedule_and_reschedule(client: TestClient, tmp_path: Path):
    # Save a test item first
    from autoclip_app.production import production_service
    item = ContentItem(id="api_test_item", title="API Schedule Test")
    production_service.storage.save_content_item(item)

    resp = client.post("/api/production/schedule", json={
        "item_id": "api_test_item",
        "platform": "youtube_shorts",
        "scheduled_time_iso": "2026-11-28T14:00:00Z",
        "rights_confirmed": True,
    })
    assert resp.status_code == 200
    sched_id = resp.json()["schedule"]["id"]

    # Reschedule
    resp_r = client.post(f"/api/production/schedules/{sched_id}/reschedule", json={
        "scheduled_time_iso": "2026-11-29T18:00:00Z",
    })
    assert resp_r.status_code == 200
    assert resp_r.json()["status"] == "rescheduled"

    # Cancel
    resp_c = client.post(f"/api/production/schedules/{sched_id}/cancel")
    assert resp_c.status_code == 200
    assert resp_c.json()["status"] == "cancelled"
