from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


@dataclass
class ChatMessage:
    author: str
    text: str
    timestamp: float  # Stream timestamp in seconds (relative to stream start or epoch)
    source: str = "unknown"  # twitch, youtube, file, simulated
    is_bot: bool = False
    emotes: list[str] = field(default_factory=list)
    caps_ratio: float = 0.0
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "author": self.author,
            "text": self.text,
            "timestamp": round(self.timestamp, 3),
            "source": self.source,
            "is_bot": self.is_bot,
            "emotes": self.emotes,
            "caps_ratio": round(self.caps_ratio, 3),
        }


@dataclass
class ChatWindowMetrics:
    window_seconds: float
    message_count: int
    mps: float  # Messages Per Second
    unique_users: int
    unique_user_ratio: float
    entropy: float  # Shannon lexical entropy
    emote_density: float
    caps_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_seconds": self.window_seconds,
            "message_count": self.message_count,
            "mps": round(self.mps, 2),
            "unique_users": self.unique_users,
            "unique_user_ratio": round(self.unique_user_ratio, 3),
            "entropy": round(self.entropy, 3),
            "emote_density": round(self.emote_density, 3),
            "caps_ratio": round(self.caps_ratio, 3),
        }


@dataclass
class SpikeDetectorConfig:
    sensitivity: Literal["low", "medium", "high"] = "medium"
    pre_roll_seconds: float = 20.0
    post_roll_seconds: float = 40.0
    coalesce_window_seconds: float = 30.0
    min_confidence: float = 55.0
    baseline_window_seconds: float = 120.0
    spam_rate_limit: int = 5  # Max messages allowed from single user in 5 seconds
    min_mps_threshold: float = 1.0  # Absolute floor to avoid triggering in totally dead chat

    @property
    def z_threshold(self) -> float:
        if self.sensitivity == "high":
            return 1.8
        if self.sensitivity == "low":
            return 3.2
        return 2.4

    @property
    def burst_multiplier(self) -> float:
        if self.sensitivity == "high":
            return 2.0
        if self.sensitivity == "low":
            return 4.0
        return 2.8


@dataclass
class SpikeEvent:
    spike_id: str = field(default_factory=lambda: f"spike_{uuid.uuid4().hex[:8]}")
    session_id: str = ""
    start_time: float = 0.0
    peak_time: float = 0.0
    end_time: float = 0.0
    baseline_mps: float = 0.0
    peak_mps: float = 0.0
    z_score: float = 0.0
    confidence_score: float = 0.0
    dominant_emotes: list[str] = field(default_factory=list)
    dominant_keywords: list[str] = field(default_factory=list)
    clip_start: float = 0.0
    clip_end: float = 0.0
    title: str = "Hype Live Moment"
    description: str = "Chat activity surge detected by AutoClip AI"
    clip_path: str | None = None
    status: str = "DETECTED"  # DETECTED, EXTRACTING, EXTRACTED, FAILED
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def duration(self) -> float:
        return max(0.0, self.clip_end - self.clip_start)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spike_id": self.spike_id,
            "session_id": self.session_id,
            "start_time": round(self.start_time, 2),
            "peak_time": round(self.peak_time, 2),
            "end_time": round(self.end_time, 2),
            "baseline_mps": round(self.baseline_mps, 2),
            "peak_mps": round(self.peak_mps, 2),
            "z_score": round(self.z_score, 2),
            "confidence_score": round(self.confidence_score, 1),
            "dominant_emotes": self.dominant_emotes,
            "dominant_keywords": self.dominant_keywords,
            "clip_start": round(self.clip_start, 2),
            "clip_end": round(self.clip_end, 2),
            "duration": round(self.duration, 2),
            "title": self.title,
            "description": self.description,
            "clip_path": self.clip_path,
            "status": self.status,
            "created_at": self.created_at,
        }
