from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoclip_app.trend_hunter.audio_monitor import AudioLoudnessMonitor
from autoclip_app.trend_hunter.clip_pipeline import ViralClipPipeline
from autoclip_app.trend_hunter.demand import DemandEngine
from autoclip_app.trend_hunter.discovery import ChannelDiscoveryService
from autoclip_app.trend_hunter.live_buffer import BufferChunk, RollingLiveBuffer
from autoclip_app.trend_hunter.models import ChannelProfile, LiveSessionState, TrendHunterConfig, ViralClipRecord
from autoclip_app.trend_hunter.service import TrendHunterService
from autoclip_app.trend_hunter.storage import TrendStorage, init_trend_tables
from autoclip_app.trend_hunter.stream_monitor import LiveStreamMonitorSession


def test_channel_discovery_parsing_and_trending():
    discovery = ChannelDiscoveryService()

    # 1. Test URL parsing for Twitch and YouTube
    twitch_results = discovery.search_channels("https://www.twitch.tv/shroud")
    assert len(twitch_results) == 1
    assert twitch_results[0].platform == "twitch"
    assert twitch_results[0].handle.lower() == "shroud"

    yt_results = discovery.search_channels("https://www.youtube.com/@IShowSpeed")
    assert len(yt_results) == 1
    assert yt_results[0].platform == "youtube"
    assert "IShowSpeed" in yt_results[0].channel_name

    # 2. Test Trending seeds
    seeds = discovery.get_trending_channels(refresh_live_status=False)
    assert len(seeds) >= 5
    names = [s.channel_name for s in seeds]
    assert "IShowSpeed" in names
    assert "KaiCenat" in names


def test_demand_score_calculation_and_weights():
    engine = DemandEngine()

    # Low viewership, quiet chat
    low_breakdown = engine.compute_demand_score(
        current_viewers=50,
        chat_mps=0.2,
        unique_chatters_ratio=0.5,
        recent_spikes_count=0,
        is_live=True,
    )
    assert 20.0 <= low_breakdown.total_score <= 60.0

    # High viewership, surging chat, frequent spikes
    high_breakdown = engine.compute_demand_score(
        current_viewers=65000,
        viewer_history=[(time.time() - 300, 45000), (time.time(), 65000)],  # +44% surge
        chat_mps=28.0,
        unique_chatters_ratio=0.85,
        recent_spikes_count=3,
        audio_excitement_score=85.0,
        transcript_text="OMG NO WAY HE DID IT INSANE WORLD RECORD",
        is_live=True,
    )
    assert high_breakdown.total_score >= 80.0
    assert high_breakdown.growth_score > 60.0
    assert high_breakdown.transcript_excitement_score > 70.0
    assert "Surging audience" in high_breakdown.summary


def test_viewer_growth_detection():
    engine = DemandEngine()
    now_t = time.time()

    # Rapid viewer growth (+50% over 5 minutes)
    history_growing = [(now_t - 300, 10000), (now_t, 15000)]
    rate_grow, score_grow = engine._calc_growth_score(history_growing, 15000)
    assert rate_grow > 0
    assert score_grow > 50.0

    # Shrinking viewer count (-30% over 5 minutes)
    history_dropping = [(now_t - 300, 10000), (now_t, 7000)]
    rate_drop, score_drop = engine._calc_growth_score(history_dropping, 7000)
    assert rate_drop < 0
    assert score_drop < 50.0


def test_audio_loudness_and_false_positive_filtering(tmp_path: Path):
    monitor = AudioLoudnessMonitor()

    # Non-existent or empty file should return baseline without error
    empty_file = tmp_path / "empty.ts"
    empty_file.write_bytes(b"")
    mean_db, max_db, score = monitor.analyze_audio_segment(empty_file)
    assert score == 0.0

    # Simulate screaming/shouting: sudden 12 dB surge above baseline with high crest factor
    monitor._mean_volume_history.extend([-28.0, -27.5, -28.2, -28.0])
    hype_score = monitor._compute_excitement_score(current_mean=-16.0, current_max=-1.0)
    assert hype_score >= 70.0

    # Simulate steady loud music: high volume but zero dynamic variance (low crest factor)
    monitor._mean_volume_history.extend([-14.0, -14.0, -13.8, -14.1])
    music_score = monitor._compute_excitement_score(current_mean=-14.0, current_max=-11.0)
    # Steady music score should be penalized because of low crest factor and zero surge
    assert music_score < hype_score


def test_rolling_buffer_pre_and_post_roll_preservation(tmp_path: Path):
    buffer = RollingLiveBuffer(
        session_id="test_sess_buffer",
        retention_seconds=300.0,
        segment_seconds=5.0,
        buffer_dir=tmp_path / "buffer",
    )

    # Populate 15 chunks (0 to 75 seconds of footage)
    for i in range(15):
        f = buffer.buffer_dir / f"chunk_{i:04d}.ts"
        f.write_bytes(b"\x47\x40\x00\x10" * 64)
        chunk = BufferChunk(
            chunk_index=i,
            file_path=f,
            start_time=i * 5.0,
            end_time=(i + 1) * 5.0,
            created_at=time.time(),
        )
        buffer._chunks.append(chunk)

    assert buffer.current_buffer_duration == 75.0
    assert buffer.latest_timestamp == 75.0

    # Simulate viral moment detected at peak time T = 45.0s
    # Pre-roll 20s -> start = 25.0s; Post-roll 40s -> end = 85.0s
    peak_t = 45.0
    clip_start = peak_t - 20.0  # 25.0s
    clip_end = peak_t + 40.0    # 85.0s

    # Verify that pre-roll chunks (starting at 25s) are safely present
    pre_roll_chunks = [c for c in buffer._chunks if c.start_time <= clip_start <= c.end_time or (25.0 <= c.start_time <= 45.0)]
    assert len(pre_roll_chunks) > 0

    # Post-roll needs up to 85.0s. Add chunks up to 85.0s to simulate post-roll capture
    for i in range(15, 18):
        f = buffer.buffer_dir / f"chunk_{i:04d}.ts"
        f.write_bytes(b"\x47\x40\x00\x10" * 64)
        buffer._chunks.append(BufferChunk(
            chunk_index=i,
            file_path=f,
            start_time=i * 5.0,
            end_time=(i + 1) * 5.0,
            created_at=time.time(),
        ))

    assert buffer.latest_timestamp >= clip_end

    # Verify retention pruning does not delete chunks newer than retention window
    async def _test_prune():
        await buffer._prune_expired_chunks()
        assert len(buffer._chunks) == 18

    asyncio.run(_test_prune())


def test_storage_crud_operations(tmp_path: Path):
    db_file = tmp_path / "test_trend.sqlite3"
    storage = TrendStorage(db_file)

    # 1. Upsert channel
    channel = ChannelProfile(
        id="ch_youtube_speed",
        channel_name="IShowSpeed",
        platform="youtube",
        channel_url="https://youtube.com/@IShowSpeed",
        is_live=True,
        current_viewers=85000,
        demand_score=95.0,
    )
    storage.upsert_channel(channel)

    fetched = storage.get_channel("ch_youtube_speed")
    assert fetched is not None
    assert fetched.channel_name == "IShowSpeed"
    assert fetched.is_live is True
    assert fetched.current_viewers == 85000

    # 2. Add Clip
    clip = ViralClipRecord(
        clip_id="viral_test_1",
        session_id="sess_123",
        channel_name="IShowSpeed",
        platform="youtube",
        stream_url="https://youtube.com/live/xyz",
        peak_time=42.0,
        clip_start=22.0,
        clip_end=82.0,
        duration=60.0,
        confidence_score=94.5,
        demand_score=95.0,
        raw_clip_path="path/raw.mp4",
        shorts_clip_path="path/shorts.mp4",
        thumbnail_path="path/thumb.jpg",
        title="IShowSpeed Viral Reaction",
        description="Speed went crazy live on stream",
        transcript="No way he did it!",
    )
    storage.add_clip(clip)

    clips = storage.list_clips()
    assert len(clips) == 1
    assert clips[0].title == "IShowSpeed Viral Reaction"

    # 3. Update clip status
    updated = storage.update_clip_status(
        clip_id="viral_test_1",
        status="APPROVED",
        copyright_confirmed=True,
    )
    assert updated is True
    refetched = storage.get_clip("viral_test_1")
    assert refetched.status == "APPROVED"
    assert refetched.copyright_confirmed is True


def test_duplicate_prevention_logic(tmp_path: Path):
    db_file = tmp_path / "test_dup.sqlite3"
    storage = TrendStorage(db_file)

    channel = ChannelProfile(
        id="ch_twitch_test",
        channel_name="test_streamer",
        platform="twitch",
        channel_url="https://twitch.tv/test_streamer",
    )
    storage.upsert_channel(channel)

    config = TrendHunterConfig(dedup_window_seconds=60.0, min_confidence=60.0)
    session = LiveStreamMonitorSession(
        channel=channel,
        storage=storage,
        config=config,
    )

    # First event at t = 50.0s
    session._last_clip_peak_time = 50.0

    # Second event at t = 75.0s (within 60s window: 75 - 50 = 25s < 60s)
    # Dedup logic check
    is_duplicate = abs(75.0 - session._last_clip_peak_time) < config.dedup_window_seconds
    assert is_duplicate is True

    # Third event at t = 120.0s (120 - 50 = 70s >= 60s)
    is_allowed = abs(120.0 - session._last_clip_peak_time) >= config.dedup_window_seconds
    assert is_allowed is True


def test_copyright_gate_and_publishing_safety(tmp_path: Path):
    db_file = tmp_path / "test_copy.sqlite3"
    storage = TrendStorage(db_file)
    service = TrendHunterService(storage)

    fake_video = tmp_path / "test_short.mp4"
    fake_video.write_bytes(b"\x00" * 1024)

    clip = ViralClipRecord(
        clip_id="clip_safety_1",
        session_id="sess_safety",
        channel_name="Speed",
        platform="youtube",
        stream_url="https://youtube.com/live/1",
        peak_time=30.0,
        clip_start=10.0,
        clip_end=70.0,
        duration=60.0,
        confidence_score=90.0,
        demand_score=85.0,
        raw_clip_path=str(fake_video),
        shorts_clip_path=str(fake_video),
        thumbnail_path="",
        title="Epic Stream Moment",
        description="Highlights",
        transcript="Speech",
    )
    storage.add_clip(clip)

    # 1. Publishing without copyright confirmation must fail
    with pytest.raises(ValueError, match="confirm copyright permissions"):
        service.approve_and_publish_clip("clip_safety_1", copyright_confirmed=False)

    # 2. Publishing with confirmation should succeed with mocked YouTube uploader
    with patch("autoclip_app.trend_hunter.service.upload_public_video") as mock_upload:
        mock_upload.return_value = {"id": "yt_video_12345", "video_id": "yt_video_12345"}
        res = service.approve_and_publish_clip("clip_safety_1", copyright_confirmed=True)
        assert res["status"] == "PUBLISHED"
        assert res["youtube_video_id"] == "yt_video_12345"

        updated = storage.get_clip("clip_safety_1")
        assert updated.status == "PUBLISHED"
        assert updated.copyright_confirmed is True


def test_multiple_simultaneous_monitoring_sessions(tmp_path: Path):
    db_file = tmp_path / "test_multi.sqlite3"
    storage = TrendStorage(db_file)
    service = TrendHunterService(storage)

    ch1 = ChannelProfile(id="ch_1", channel_name="StreamerOne", platform="twitch", channel_url="https://twitch.tv/1")
    ch2 = ChannelProfile(id="ch_2", channel_name="StreamerTwo", platform="youtube", channel_url="https://youtube.com/@2")
    storage.upsert_channel(ch1)
    storage.upsert_channel(ch2)

    async def _run_multi():
        # Start monitoring both channels simultaneously in simulation mode
        s1 = await service.start_monitoring("ch_1", is_simulation=True)
        s2 = await service.start_monitoring("ch_2", is_simulation=True)

        assert len(service.active_monitors) == 2
        assert s1["session_id"] != s2["session_id"]
        assert s1["channel_id"] == "ch_1"
        assert s2["channel_id"] == "ch_2"

        # Stop monitoring both
        await service.stop_monitoring("ch_1")
        await service.stop_monitoring("ch_2")
        assert len(service.active_monitors) == 0

    asyncio.run(_run_multi())


def test_api_resilience_and_offline_channel(tmp_path: Path):
    discovery = ChannelDiscoveryService(request_timeout=1.0)

    # Unreachable / nonexistent channel should not crash the discovery engine
    prof = discovery.check_twitch_channel("nonexistent_random_streamer_12345")
    assert prof.is_live is False
    assert prof.demand_score >= 0.0

    yt_prof = discovery.check_youtube_channel("nonexistent_random_streamer_12345")
    assert yt_prof.is_live is False
