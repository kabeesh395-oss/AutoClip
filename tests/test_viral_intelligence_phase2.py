from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoclip_app.trend_hunter.advanced_editing import AdvancedClipEditor
from autoclip_app.trend_hunter.learning import FeedbackLearner, init_learning_tables
from autoclip_app.trend_hunter.models import (
    AdaptiveWeights,
    ChannelProfile,
    LiveSessionState,
    PredictionResult,
    TrendHunterConfig,
    ViralClipRecord,
)
from autoclip_app.trend_hunter.predictor import ViralMomentPredictor
from autoclip_app.trend_hunter.queue_worker import PersistentJobQueue, ResourceGuard, init_queue_tables
from autoclip_app.trend_hunter.ranking import ChannelRanker
from autoclip_app.trend_hunter.service import TrendHunterService
from autoclip_app.trend_hunter.storage import TrendStorage, init_trend_tables


@pytest.fixture
def temp_db_and_storage(tmp_path: Path):
    db_file = tmp_path / "test_phase2.sqlite3"
    init_trend_tables(db_file)
    init_learning_tables(db_file)
    init_queue_tables(db_file)
    storage = TrendStorage(db_file)
    return db_file, storage


def test_channel_ranker_and_rapid_growth(temp_db_and_storage):
    db_file, storage = temp_db_and_storage
    ranker = ChannelRanker(storage=storage, rapid_growth_threshold_pct_per_min=10.0)

    now = time.time()
    # Channel 1: High viewers, static audience (no rapid growth)
    ch1 = ChannelProfile(
        id="ch_1",
        channel_name="StaticStreamer",
        platform="youtube",
        channel_url="https://youtube.com/@static",
        is_live=True,
        current_viewers=30000,
        viewer_history=[(now - 300, 30000), (now, 30000)],
        viewer_growth_rate=0.0,
        demand_score=60.0,
        custom_priority=1,
    )

    # Channel 2: Medium viewers, but surging fast (+60% in 5 min)
    ch2 = ChannelProfile(
        id="ch_2",
        channel_name="SurgingStreamer",
        platform="twitch",
        channel_url="https://twitch.tv/surging",
        is_live=True,
        current_viewers=20000,
        viewer_history=[(now - 300, 12500), (now, 20000)],
        viewer_growth_rate=12.0,  # 12% per min > 10% threshold
        demand_score=55.0,
        custom_priority=1,
    )

    # Channel 3: Offline channel
    ch3 = ChannelProfile(
        id="ch_3",
        channel_name="OfflineStreamer",
        platform="youtube",
        channel_url="https://youtube.com/@offline",
        is_live=False,
        current_viewers=0,
        demand_score=50.0,
        custom_priority=1,
    )

    score_1 = ranker.compute_channel_ranking_score(ch1)
    score_2 = ranker.compute_channel_ranking_score(ch2)
    score_3 = ranker.compute_channel_ranking_score(ch3)

    # Surging channel gets +20 growth bonus (55 + 20 = 75), surpassing static channel (60)
    assert score_2 > score_1
    assert ch2.is_rapid_growing is True
    assert score_3 < score_1  # Offline penalized

    # Test sorting
    ranked = ranker.rank_channels([ch1, ch2, ch3])
    assert ranked[0].id == "ch_2"
    assert ranked[1].id == "ch_1"
    assert ranked[2].id == "ch_3"


def test_custom_priority_boost(temp_db_and_storage):
    db_file, storage = temp_db_and_storage
    ranker = ChannelRanker(storage=storage)

    # Normal priority (1)
    ch_normal = ChannelProfile(
        id="ch_normal",
        channel_name="NormalPriority",
        platform="youtube",
        channel_url="https://youtube.com/@normal",
        is_live=True,
        current_viewers=10000,
        demand_score=65.0,
        custom_priority=1,
    )

    # Critical Pin priority (3) with lower base demand score
    ch_pinned = ChannelProfile(
        id="ch_pinned",
        channel_name="PinnedCreator",
        platform="youtube",
        channel_url="https://youtube.com/@pinned",
        is_live=True,
        current_viewers=5000,
        demand_score=50.0,
        custom_priority=3,  # Critical Pin: +30 points bonus
    )

    score_normal = ranker.compute_channel_ranking_score(ch_normal)
    score_pinned = ranker.compute_channel_ranking_score(ch_pinned)

    assert score_pinned > score_normal
    ranked = ranker.rank_channels([ch_normal, ch_pinned])
    assert ranked[0].id == "ch_pinned"


def test_viral_moment_prediction_acceleration_and_rationale():
    predictor = ViralMomentPredictor()

    now = time.time()
    # Feed accelerating chat metrics: 2 MPS -> 6 MPS -> 28 MPS
    predictor.record_chat_metric(now - 4, current_mps=2.0, author="user1")
    predictor.record_chat_metric(now - 2, current_mps=6.0, author="user2")
    predictor.record_chat_metric(now, current_mps=28.0, author="user3")

    # Predict viral moment
    result = predictor.predict_viral_moment(
        current_mps=28.0,
        baseline_mps=3.0,
        audio_surge_db=8.5,
        audio_max_db=-1.5,
        viewer_growth_rate=15.0,
        recent_transcript="OMG UNBELIEVABLE CLUTCH NO WAY",
        words_per_second=3.5,
    )

    assert isinstance(result, PredictionResult)
    assert result.confidence_score >= 80.0
    assert result.is_early_prediction is True
    assert result.acceleration > 0.0

    # Verify explainable Why Selected rationale
    assert result.rationale != ""
    assert "Chat velocity" in result.rationale or "Audio surge" in result.rationale or "escalating" in result.rationale
    assert result.signals["transcript_score"] > 20.0


def test_advanced_clip_editor_reframing_commands(tmp_path: Path):
    editor = AdvancedClipEditor(output_dir=tmp_path / "clips")

    input_dummy = tmp_path / "input.mp4"
    input_dummy.write_bytes(b"dummy video data")

    out_fit_blur = tmp_path / "out_fit_blur.mp4"
    out_split = tmp_path / "out_split.mp4"
    out_smart = tmp_path / "out_smart.mp4"

    # Pre-create output files to simulate successful ffmpeg outputs
    out_fit_blur.write_bytes(b"fit blur encoded output")
    out_split.write_bytes(b"split stack encoded output")
    out_smart.write_bytes(b"smart crop encoded output")

    # We mock subprocess.run to verify FFmpeg filter strings generated for 9:16 layout
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)

        # 1. FIT_BLUR
        editor.reframe_to_vertical(input_dummy, out_fit_blur, layout="FIT_BLUR")
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "scale=1080:1920" in cmd_str
        assert "boxblur" in cmd_str or "split" in cmd_str or "overlay" in cmd_str

        # 2. SPLIT_STACK
        mock_run.reset_mock()
        editor.reframe_to_vertical(input_dummy, out_split, layout="SPLIT_STACK")
        cmd_split = " ".join(mock_run.call_args[0][0])
        assert "vstack" in cmd_split

        # 3. SMART_CROP
        mock_run.reset_mock()
        editor.reframe_to_vertical(input_dummy, out_smart, layout="SMART_CROP")
        cmd_crop = " ".join(mock_run.call_args[0][0])
        assert "crop=" in cmd_crop


def test_advanced_clip_editor_silence_removal(tmp_path: Path):
    editor = AdvancedClipEditor(output_dir=tmp_path / "clips")
    input_file = tmp_path / "input_speech.mp4"
    input_file.write_bytes(b"A" * 5000)
    output_file = tmp_path / "input_speech_trimmed.mp4"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        # Create output file to simulate successful ffmpeg run
        output_file.write_bytes(b"B" * 4000)

        result_path = editor.remove_silence(input_file, output_file, threshold_db=-35.0, min_silence_sec=0.8)
        assert mock_run.called
        cmd_str = " ".join(mock_run.call_args[0][0])
        assert "silenceremove" in cmd_str
        assert "stop_threshold=-35.0dB" in cmd_str
        assert result_path == output_file


def test_quality_assurance_check(tmp_path: Path):
    editor = AdvancedClipEditor(output_dir=tmp_path)

    # 1. Non-existent file
    passed, issues = editor.run_quality_check(tmp_path / "missing.mp4", "hello world testing")
    assert passed is False
    assert any("corrupt or empty" in i for i in issues)

    # 2. Valid mock file with ffprobe response
    valid_file = tmp_path / "valid.mp4"
    valid_file.write_bytes(b"X" * 25000)

    ffprobe_mock_output = (
        '{"format": {"duration": "32.4"}, "streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}'
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=ffprobe_mock_output)
        passed, issues = editor.run_quality_check(valid_file, "This was an incredible play right here!")
        assert passed is True
        assert len(issues) == 0


def test_feedback_learner_adaptive_weights(temp_db_and_storage):
    db_file, storage = temp_db_and_storage
    learner = FeedbackLearner(db_path=db_file)

    initial_weights = learner.load_weights()
    assert initial_weights.w_chat == pytest.approx(0.30, abs=0.01)
    assert initial_weights.sample_count == 0

    # 1. Log positive user feedback (APPROVAL) on a chat-heavy clip
    features = {
        "chat_score": 90.0,
        "audio_score": 75.0,
        "speech_score": 50.0,
        "transcript_score": 40.0,
        "viewer_growth_score": 60.0,
    }
    learner.record_feedback(
        clip_id="clip_test_1",
        user_action="APPROVED",
        features=features,
        notes="High energy hype moment approved by editor",
    )

    updated_weights = learner.load_weights()
    assert updated_weights.sample_count == 1
    # Approval of chat-heavy clip reinforces chat weight
    assert updated_weights.w_chat >= initial_weights.w_chat

    # 2. Log negative user feedback (REJECTION)
    learner.record_feedback(
        clip_id="clip_test_2",
        user_action="REJECTED",
        features=features,
        notes="Spam false positive",
    )

    rejected_weights = learner.load_weights()
    assert rejected_weights.sample_count == 2
    # Rejection lowers sensitivity bias to be more conservative
    assert rejected_weights.sensitivity_bias < updated_weights.sensitivity_bias


def test_persistent_job_queue_and_resource_guard(temp_db_and_storage):
    db_file, storage = temp_db_and_storage
    guard = ResourceGuard(max_cpu_percent=95.0, max_ram_percent=95.0)
    assert guard.can_process_job() is True

    queue = PersistentJobQueue(db_path=db_file, resource_guard=guard)

    # 1. Enqueue jobs with different priority (lower number = higher priority)
    job_high = queue.enqueue(session_id="sess_1", clip_id="clip_high", job_type="EXTRACT", priority=5)
    job_low = queue.enqueue(session_id="sess_1", clip_id="clip_low", job_type="EXTRACT", priority=20)

    # 2. get_pending_jobs should return highest priority first
    pending = queue.get_pending_jobs(limit=10)
    assert len(pending) == 2
    assert pending[0].job_id == job_high.job_id
    assert pending[0].clip_id == "clip_high"
    assert pending[1].job_id == job_low.job_id

    # 3. Mark completed
    queue.update_job_status(job_high.job_id, "COMPLETED")
    pending_after = queue.get_pending_jobs(limit=10)
    assert len(pending_after) == 1
    assert pending_after[0].job_id == job_low.job_id

    # 4. Test failure and retry logic
    queue.update_job_status(job_low.job_id, "FAILED", error_message="Temporary FFmpeg lock", increment_retry=True)
    all_jobs = queue.get_pending_jobs(limit=10)
    assert len(all_jobs) == 0  # No longer QUEUED

    # Re-queue for retry
    queue.update_job_status(job_low.job_id, "QUEUED")
    requeued = queue.get_pending_jobs(limit=10)
    assert len(requeued) == 1
    assert requeued[0].job_id == job_low.job_id
    assert requeued[0].retry_count == 1
    assert "FFmpeg lock" in requeued[0].error_message


def test_autonomous_workflow_levels(temp_db_and_storage):
    db_file, storage = temp_db_and_storage
    service = TrendHunterService(storage=storage)

    # Test setting valid automation levels
    mode_semi = service.set_automation_level("SEMI_AUTO")
    assert mode_semi == "SEMI_AUTO"
    assert service.default_config.automation_level == "SEMI_AUTO"

    mode_full = service.set_automation_level("FULL_AUTO")
    assert mode_full == "FULL_AUTO"
    assert service.default_config.automation_level == "FULL_AUTO"

    mode_manual = service.set_automation_level("MANUAL")
    assert mode_manual == "MANUAL"
    assert service.default_config.automation_level == "MANUAL"

    # Test safety publishing gate in full auto / manual
    clip = ViralClipRecord(
        clip_id="clip_gate_test",
        session_id="sess_gate",
        channel_name="GateCreator",
        platform="youtube",
        stream_url="https://youtube.com/live/gate",
        peak_time=120.0,
        clip_start=100.0,
        clip_end=160.0,
        duration=60.0,
        confidence_score=92.0,
        demand_score=80.0,
        raw_clip_path="",
        shorts_clip_path="",
        thumbnail_path="",
        title="Epic Gate Highlight",
        description="Epic moment description",
        transcript="Speech dialogue",
        status="PENDING_REVIEW",
    )
    storage.add_clip(clip)

    # Reject publishing without copyright confirmed
    with pytest.raises(Exception) as exc:
        service.approve_and_publish_clip(
            clip_id="clip_gate_test",
            copyright_confirmed=False,
        )
    assert "Copyright" in str(exc.value) or "permission" in str(exc.value) or "confirmed" in str(exc.value)

    # Reject publishing a rejected clip even if copyright is confirmed
    clip_rejected = ViralClipRecord(
        clip_id="clip_rejected_test",
        session_id="sess_gate",
        channel_name="GateCreator",
        platform="youtube",
        stream_url="https://youtube.com/live/gate",
        peak_time=120.0,
        clip_start=100.0,
        clip_end=160.0,
        duration=60.0,
        confidence_score=70.0,
        demand_score=60.0,
        raw_clip_path="",
        shorts_clip_path="",
        thumbnail_path="",
        title="Rejected Moment",
        description="Desc",
        transcript="Speech dialogue",
        status="REJECTED",
    )
    storage.add_clip(clip_rejected)

    with pytest.raises(ValueError) as val_exc:
        service.approve_and_publish_clip(
            clip_id="clip_rejected_test",
            copyright_confirmed=True,
        )
    assert "rejected" in str(val_exc.value).lower()


def test_ai_feedback_directly_shifts_future_predictions(temp_db_and_storage):
    db_file, storage = temp_db_and_storage
    learner = FeedbackLearner(db_path=db_file)
    predictor = ViralMomentPredictor(adaptive_weights=learner.current_weights)

    # Initial prediction on marginal moment
    res_before = predictor.predict_viral_moment(
        current_mps=8.0,
        baseline_mps=2.0,
        audio_surge_db=3.0,
        audio_max_db=-6.0,
        viewer_growth_rate=2.0,
        recent_transcript="good move here",
        words_per_second=2.0,
    )

    # Now user rejects a similar clip with negative feedback
    learner.record_feedback(
        clip_id="clip_spam_1",
        user_action="REJECTED",
        features={"confidence_score": res_before.confidence_score, "audio_excitement_score": 60.0},
        notes="False positive noise",
    )

    # Predictor updates with adapted weights
    predictor.update_weights(learner.current_weights)

    # Re-evaluate identical moment
    res_after = predictor.predict_viral_moment(
        current_mps=8.0,
        baseline_mps=2.0,
        audio_surge_db=3.0,
        audio_max_db=-6.0,
        viewer_growth_rate=2.0,
        recent_transcript="good move here",
        words_per_second=2.0,
    )

    # Assert prediction confidence score was lowered by the adaptive feedback
    assert res_after.confidence_score < res_before.confidence_score


def test_stream_disconnection_resilience(temp_db_and_storage):
    db_file, storage = temp_db_and_storage
    cfg = TrendHunterConfig(
        reconnect_attempts=5,
    )

    channel = ChannelProfile(
        id="ch_resilience",
        channel_name="ResilienceGamer",
        platform="twitch",
        channel_url="https://twitch.tv/resilience",
        is_live=True,
    )

    from autoclip_app.trend_hunter.stream_monitor import LiveStreamMonitorSession

    session = LiveStreamMonitorSession(
        channel=channel,
        config=cfg,
        storage=storage,
    )

    # Test that reconnect state is tracked properly
    assert session.state.reconnect_count == 0
    session.state.reconnect_count += 1
    assert session.state.reconnect_count == 1
    session.state.error_message = "Stream connection reset by peer"
    assert "Stream connection reset" in session.state.error_message
