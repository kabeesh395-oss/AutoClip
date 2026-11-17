from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from autoclip_app.chat_engine import (
    ChatMessage,
    ChatSpikeDetector,
    ChatSpikeEngineService,
    ChatWindowMetrics,
    FileReplayChatSource,
    SimulatedStreamChatSource,
    SpikeDetectorConfig,
    SpikeEvent,
    create_chat_session,
    get_chat_session_summary,
    init_chat_tables,
    insert_chat_message,
    insert_chat_spike,
    list_chat_spikes,
)
from autoclip_app.chat_engine.clip_extractor import ChatClipExtractor
from autoclip_app.chat_engine.detector import compute_shannon_entropy


def test_models_and_metrics():
    msg = ChatMessage(author="viewer1", text="LET'S GOOO POGGERS 🔥", timestamp=12.5, emotes=["poggers", "🔥"], caps_ratio=0.8)
    d = msg.to_dict()
    assert d["author"] == "viewer1"
    assert d["timestamp"] == 12.5
    assert "poggers" in d["emotes"]

    metrics = ChatWindowMetrics(
        window_seconds=10.0,
        message_count=35,
        mps=3.5,
        unique_users=25,
        unique_user_ratio=0.714,
        entropy=0.88,
        emote_density=0.45,
        caps_ratio=0.3,
    )
    md = metrics.to_dict()
    assert md["mps"] == 3.5
    assert md["unique_users"] == 25

    spike = SpikeEvent(
        session_id="sess_1",
        start_time=10.0,
        peak_time=15.0,
        end_time=20.0,
        baseline_mps=0.8,
        peak_mps=8.5,
        z_score=4.2,
        confidence_score=85.0,
        dominant_emotes=["POG", "W"],
        dominant_keywords=["Clutch"],
        clip_start=0.0,
        clip_end=45.0,
    )
    assert spike.duration == 45.0
    sd = spike.to_dict()
    assert sd["peak_mps"] == 8.5
    assert sd["z_score"] == 4.2


def test_entropy_computation():
    # Copypasta: 1 single repeated phrase -> low entropy
    low_words = ["copypasta"] * 50
    h_low = compute_shannon_entropy(low_words)
    assert h_low == 0.0

    # Diverse reactions -> high entropy
    diverse_words = ["insane", "play", "no", "way", "bro", "he", "actually", "did", "that", "unbelievable"]
    h_high = compute_shannon_entropy(diverse_words)
    assert h_high > 0.8


def test_anti_spam_and_bot_filtering():
    detector = ChatSpikeDetector(SpikeDetectorConfig(spam_rate_limit=3))

    # Bot message should be dropped
    bot_msg = ChatMessage(author="Nightbot", text="Follow the streamer on Twitter!", timestamp=1.0, is_bot=True)
    assert detector.feed_message(bot_msg) is None
    assert detector.total_messages_filtered == 1

    # Single user spamming 10 messages within 2 seconds
    for i in range(10):
        m = ChatMessage(author="spammer99", text="SPAM SPAM SPAM", timestamp=2.0 + (i * 0.1))
        detector.feed_message(m)

    # First 3 allowed, remaining 7 filtered out
    assert detector.total_messages_filtered >= 7


def test_file_replay_chat_source(tmp_path: Path):
    async def _run():
        jsonl_file = tmp_path / "chat_log.jsonl"
        jsonl_file.write_text("\n".join([
            json.dumps({"author": "alice", "text": "hello chat", "timestamp": 1.0}),
            json.dumps({"author": "bob", "text": "hey alice! POG", "timestamp": 2.5}),
            json.dumps({"author": "charlie", "text": "clutch play W", "timestamp": 4.0}),
        ]))

        src = FileReplayChatSource(jsonl_file, playback_speed=0.0)
        await src.connect()
        collected = []
        async for m in src.iter_messages():
            collected.append(m)
        await src.disconnect()

        assert len(collected) == 3
        assert collected[0].author == "alice"
        assert "pog" in collected[1].emotes
        assert "w" in collected[2].emotes

    asyncio.run(_run())


def test_simulated_stream_chat_source():
    async def _run():
        sim = SimulatedStreamChatSource(
            duration_seconds=10.0,
            baseline_mps=2.0,
            spike_schedule=[{"start": 3.0, "end": 5.0, "mps": 8.0, "phrases": ["POG 🔥"]}],
            instant=True
        )
        await sim.connect()
        msgs = []
        async for m in sim.iter_messages():
            msgs.append(m)
        await sim.disconnect()

        assert len(msgs) >= 15
        spike_msgs = [m for m in msgs if 3.0 <= m.timestamp <= 5.0]
        assert len(spike_msgs) >= 8

    asyncio.run(_run())


def test_spike_detection_burst_and_coalescing():
    config = SpikeDetectorConfig(
        sensitivity="high",
        min_mps_threshold=2.0,
        pre_roll_seconds=10.0,
        post_roll_seconds=20.0,
        coalesce_window_seconds=15.0,
    )
    detector = ChatSpikeDetector(config=config, session_id="test_sess")

    # 1. Warm-up baseline activity (1 msg/sec from 0s to 30s)
    users = [f"user_{i}" for i in range(20)]
    for sec in range(0, 30):
        detector.feed_message(ChatMessage(author=users[sec % 20], text="just chilling", timestamp=float(sec)))

    baseline_mean, _ = detector.get_baseline_stats()
    assert baseline_mean >= 0.3

    # 2. Sudden huge burst of 25 messages in 4 seconds (t=31s to t=34s) with hype emotes
    hype_phrases = ["POGGERS 🔥", "W STREAMER", "NO WAY!!", "CLUTCH", "HOLY PLAY", "OMEGALUL"]
    spike_detected = None
    for i in range(25):
        t = 31.0 + (i * 0.15)
        u = f"viewer_{i % 15}"
        p = hype_phrases[i % len(hype_phrases)]
        emotes, caps = [e for e in ["pog", "w", "🔥"] if e in p.lower()], 0.5
        res = detector.feed_message(ChatMessage(author=u, text=p, timestamp=t, emotes=emotes, caps_ratio=caps))
        if res:
            spike_detected = res

    assert spike_detected is not None
    assert spike_detected.confidence_score >= 50.0
    assert spike_detected.peak_mps >= 3.0
    assert spike_detected.clip_start <= 31.0
    assert spike_detected.clip_end >= 45.0

    # 3. Follow-up spike within coalesce window (t=40s)
    follow_up_res = None
    for i in range(15):
        t = 40.0 + (i * 0.2)
        res = detector.feed_message(ChatMessage(author=f"viewer_{i % 10}", text="CONTINUED HYPE W W W", timestamp=t, emotes=["w"]))
        if res:
            follow_up_res = res

    # Should have coalesced into the same spike rather than creating a duplicate
    assert len(detector.detected_spikes) == 1
    assert detector.detected_spikes[0].clip_end >= 55.0


def test_sqlite_storage(tmp_path: Path):
    db_file = tmp_path / "test_chat.sqlite3"
    init_chat_tables(db_file)

    sess = create_chat_session("sess_abc", "https://twitch.tv/shroud", "twitch", db_path=db_file)
    assert sess["status"] == "ACTIVE"

    msg = ChatMessage(author="ninja", text="GG WP", timestamp=5.2, emotes=["gg"])
    insert_chat_message("sess_abc", msg, is_spike=False, db_path=db_file)

    spike = SpikeEvent(
        spike_id="spk_99",
        session_id="sess_abc",
        start_time=10.0,
        peak_time=15.0,
        end_time=18.0,
        baseline_mps=1.0,
        peak_mps=12.0,
        z_score=5.1,
        confidence_score=92.0,
        dominant_emotes=["GG", "W"],
        dominant_keywords=["Clutch"],
        clip_start=0.0,
        clip_end=40.0,
        title="Epic Clutch",
    )
    insert_chat_spike(spike, db_path=db_file)

    spikes = list_chat_spikes("sess_abc", db_path=db_file)
    assert len(spikes) == 1
    assert spikes[0]["id"] == "spk_99"
    assert spikes[0]["peak_mps"] == 12.0
    assert "GG" in spikes[0]["dominant_emotes"]

    summary = get_chat_session_summary("sess_abc", db_path=db_file)
    assert summary is not None
    assert summary["total_messages"] == 1
    assert summary["total_spikes"] == 1


def test_clip_extractor_with_real_video(tmp_path: Path):
    sample_video = Path("data/sample_real_video.mp4")
    if not sample_video.exists():
        sample_video = Path("data/speed_segment_120s.mp4")

    if sample_video.exists():
        extractor = ChatClipExtractor(output_dir=tmp_path / "clips")
        spike = SpikeEvent(
            spike_id="spike_test_extract",
            session_id="sess_ext",
            start_time=2.0,
            peak_time=5.0,
            end_time=8.0,
            clip_start=1.0,
            clip_end=6.0,
        )
        clip_path = extractor.extract_clip(sample_video, spike)
        assert clip_path.exists()
        assert clip_path.stat().st_size > 1000
        assert spike.status == "EXTRACTED"


def test_chat_engine_service_flow(tmp_path: Path):
    async def _run():
        service = ChatSpikeEngineService()
        config = SpikeDetectorConfig(
            sensitivity="high",
            min_mps_threshold=2.0,
            pre_roll_seconds=5.0,
            post_roll_seconds=10.0,
        )
        sample_video = Path("data/sample_real_video.mp4")
        if not sample_video.exists():
            sample_video = Path("data/speed_segment_120s.mp4")

        # Start simulated session
        status = await service.start_session(
            stream_target="simulated_stream",
            source_type="simulated",
            video_path=sample_video if sample_video.exists() else None,
            config=config,
            session_id=f"sess_{tmp_path.name[:6]}",
        )
        assert status["session_id"] is not None
        assert status["is_active"] is True

        # Wait briefly for simulated messages to process
        if service._monitor_task:
            await service._monitor_task

        summary = service.get_status()
        assert summary["total_messages"] >= 50
        assert summary["total_spikes"] >= 1

        spikes = service.list_spikes()
        assert len(spikes) >= 1
        assert spikes[0]["confidence_score"] > 50.0

        await service.stop_session()

    asyncio.run(_run())
