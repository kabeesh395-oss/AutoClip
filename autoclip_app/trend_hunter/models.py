from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ChannelProfile:
    id: str
    channel_name: str
    platform: str  # 'youtube' | 'twitch'
    channel_url: str
    stream_url: str = ""
    handle: str = ""
    is_live: bool = False
    current_viewers: int = 0
    viewer_history: list[tuple[float, int]] = field(default_factory=list)  # (timestamp, viewers)
    viewer_growth_rate: float = 0.0  # viewers/min or percentage change
    demand_score: float = 0.0  # 0.0 - 100.0
    category: str = "General"
    stream_title: str = ""
    thumbnail_url: str = ""
    last_checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    auto_monitor: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DemandScoreBreakdown:
    total_score: float = 0.0
    viewer_score: float = 0.0
    growth_score: float = 0.0
    chat_mps_score: float = 0.0
    unique_chatters_score: float = 0.0
    spike_frequency_score: float = 0.0
    audio_excitement_score: float = 0.0
    transcript_excitement_score: float = 0.0
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrendHunterConfig:
    pre_roll_seconds: float = 20.0
    post_roll_seconds: float = 40.0
    buffer_retention_seconds: float = 300.0  # 5 minutes ring buffer
    sensitivity: str = "medium"  # 'low' | 'medium' | 'high'
    min_confidence: float = 60.0
    dedup_window_seconds: float = 60.0
    layout: str = "9:16"  # '9:16' | '16:9'
    caption_style: str = "KINETIC_YELLOW"
    auto_publish: bool = False  # Strict default for copyright safety
    max_concurrent_streams: int = 3

    def get_sensitivity_z_threshold(self) -> float:
        thresholds = {"low": 3.0, "medium": 2.2, "high": 1.6}
        return thresholds.get(self.sensitivity.lower(), 2.2)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LiveSessionState:
    session_id: str
    channel_id: str
    channel_name: str
    platform: str
    stream_url: str
    status: str = "INITIALIZING"  # INITIALIZING | MONITORING | EXTRACTING | STOPPED | ERROR
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    stopped_at: str | None = None
    current_viewers: int = 0
    viewer_growth: float = 0.0
    current_mps: float = 0.0
    current_audio_db: float = -60.0
    current_demand_score: float = 0.0
    spikes_detected_count: int = 0
    clips_extracted_count: int = 0
    buffer_duration_seconds: float = 0.0
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ViralClipRecord:
    clip_id: str
    session_id: str
    channel_name: str
    platform: str
    stream_url: str
    peak_time: float
    clip_start: float
    clip_end: float
    duration: float
    confidence_score: float
    demand_score: float
    raw_clip_path: str
    shorts_clip_path: str
    thumbnail_path: str
    title: str
    description: str
    transcript: str
    titles: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    status: str = "PENDING_REVIEW"  # PENDING_REVIEW | APPROVED | PUBLISHED | REJECTED
    copyright_confirmed: bool = False
    youtube_video_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
