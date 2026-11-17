from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class PlatformType(str, Enum):
    YOUTUBE_SHORTS = "youtube_shorts"
    YOUTUBE_LONGFORM = "youtube_longform"
    INSTAGRAM_REELS = "instagram_reels"


class PublishStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    UPLOADING = "uploading"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PrivacyStatus(str, Enum):
    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


@dataclass
class ContentItem:
    id: str
    source_clip_id: str | None = None
    title: str = ""
    title_options: list[str] = field(default_factory=list)
    description: str = ""
    hashtags: list[str] = field(default_factory=list)
    video_path_vertical: str | None = None
    video_path_horizontal: str | None = None
    thumbnail_path: str | None = None
    duration_seconds: float = 0.0
    dialogue_quality_score: float = 100.0
    sha256_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PublishSchedule:
    id: str
    item_id: str
    platform: str = "youtube_shorts"
    scheduled_time_iso: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    privacy_status: str = "private"
    status: str = "scheduled"
    external_id: str | None = None
    external_url: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    error_message: str | None = None
    rights_confirmed: bool = False
    published_at: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlatformAnalytics:
    id: str
    item_id: str
    platform: str
    external_id: str
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    watch_time_seconds: float = 0.0
    retention_rate: float = 0.0
    engagement_rate: float = 0.0
    raw_metrics: dict[str, Any] = field(default_factory=dict)
    last_synced_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProductionRules:
    daily_limit_shorts: int = 3
    daily_limit_longform: int = 1
    daily_limit_reels: int = 3
    preferred_slots: list[str] = field(default_factory=lambda: ["12:00", "16:00", "20:00"])
    min_quality_score: float = 60.0
    require_rights_confirmation: bool = True
    full_auto_enabled: bool = False
    auto_schedule_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
