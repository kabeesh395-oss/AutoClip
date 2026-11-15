from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autoclip_app.batch_manager import (
    add_source_to_batch,
    create_batch,
    create_creator,
    get_batch,
    init_batch_tables,
    register_game_policy,
)
from autoclip_app.batch_worker import BatchWorkerPool, process_batch_job
from autoclip_app.pipeline import Candidate, Segment


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_batch_worker.sqlite3"
    init_batch_tables(db_file)
    return db_file


def test_batch_worker_stalled_job_recovery(test_db):
    creator = create_creator("SpeedStreamer", "https://youtube.com/@speed", db_path=test_db)
    batch = create_batch("Recovery Test Batch", db_path=test_db)
    source_res = add_source_to_batch(batch["id"], "https://example.com/v1", creator_id=creator["id"], db_path=test_db)

    batch_data = get_batch(batch["id"], db_path=test_db)
    job_id = batch_data["jobs"][0]["id"]

    # Simulate power cut / unexpected crash while rendering
    from autoclip_app.batch_manager import update_job_status
    update_job_status(job_id, "RENDERING", progress=80, stage_message="Rendering crashed", db_path=test_db)

    worker_pool = BatchWorkerPool(max_concurrency=2, db_path=test_db)
    worker_pool._recover_stalled_jobs()

    batch_recovered = get_batch(batch["id"], db_path=test_db)
    recovered_job = batch_recovered["jobs"][0]
    assert recovered_job["status"] == "QUEUED"
    assert "Resumed from system restart" in recovered_job["stage_message"]


def test_process_batch_job_rights_blocked(test_db):
    creator = create_creator("SuspendedGamer", "https://youtube.com/@suspended", permission_status="REVOKED", db_path=test_db)
    batch = create_batch("Blocked Rights Batch", db_path=test_db)
    source_res = add_source_to_batch(batch["id"], "https://example.com/v2", creator_id=creator["id"], db_path=test_db)
    assert source_res["rights_status"] == "BLOCKED"

    batch_data = get_batch(batch["id"], db_path=test_db)
    job_id = batch_data["jobs"][0]["id"]

    result = process_batch_job(job_id, db_path=test_db)
    assert result["status"] == "FAILED"

    batch_data = get_batch(batch["id"], db_path=test_db)
    assert batch_data["jobs"][0]["status"] == "FAILED"
    assert "Rights BLOCKED" in batch_data["jobs"][0]["error_message"]


def test_process_batch_job_full_pipeline(tmp_path, test_db, monkeypatch):
    # 1. Create a 4-second synthetic mp4 with audio
    synth_video = tmp_path / "source_stream.mp4"
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-f", "lavfi", "-i", "color=c=purple:s=640x360:d=4,format=yuv420p",
        "-f", "lavfi", "-i", "sine=frequency=520:duration=4",
        "-c:v", "libx264", "-c:a", "aac", str(synth_video)
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    # 2. Mock AI transcription and highlight selection
    mock_rows = [
        Segment(0.1, 1.5, "Welcome to the grand finals.", ()),
        Segment(1.8, 3.5, "Unbelievable trick shot to secure the round.", ())
    ]
    mock_candidates = [
        Candidate(0.0, 1.8, "Opening Moment", "Great introduction", 92.0, "Welcome to the grand finals."),
        Candidate(1.8, 3.8, "Final Round Clutch", "Insane trick shot", 98.0, "Unbelievable trick shot to secure the round."),
    ]

    monkeypatch.setattr("autoclip_app.batch_worker.transcribe", lambda *a, **k: mock_rows)
    monkeypatch.setattr("autoclip_app.batch_worker.choose_highlights", lambda *a, **k: mock_candidates)

    def fake_post(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr("requests.post", fake_post)

    # 3. Create creator, game policy, and batch
    creator = create_creator("ProGamer", "https://youtube.com/@progamer", db_path=test_db)
    register_game_policy("Apex", "Respawn", "VERIFIED", 1, db_path=test_db)
    batch = create_batch("Pro Tournament Batch", target_videos_per_day=3, schedule_days=3,
                         daily_shorts=2, daily_long=1, db_path=test_db)

    # 4. Add source pointing to synthetic local video
    source_res = add_source_to_batch(
        batch_id=batch["id"],
        source_url=str(synth_video),
        creator_id=creator["id"],
        game_name="Apex",
        title="Apex Grand Finals Stream",
        db_path=test_db
    )
    assert source_res["rights_status"] == "PASS"

    batch_data = get_batch(batch["id"], db_path=test_db)
    job_id = batch_data["jobs"][0]["id"]

    # 5. Execute batch processing
    result = process_batch_job(job_id, db_path=test_db)
    assert result["status"] == "AWAITING_APPROVAL"
    assert len(result["clips"]) == 3  # 2 Shorts + 1 Long compilation

    # 6. Verify database records
    final_batch = get_batch(batch["id"], db_path=test_db)
    assert final_batch["status"] == "REVIEW_READY"

    job = final_batch["jobs"][0]
    assert job["status"] == "AWAITING_APPROVAL"
    assert job["progress"] == 100

    clips = final_batch["clips"]
    assert len(clips) == 3
    shorts = [c for c in clips if c["format"] == "SHORT"]
    longs = [c for c in clips if c["format"] == "LONG"]
    assert len(shorts) == 2
    assert len(longs) == 1

    for c in clips:
        # Mandatory: 100% manual approval required
        assert c["approval_status"] == "PENDING"
        assert c["rights_status"] == "PASS"
        assert Path(c["output_path"]).exists()
        assert Path(c["output_path"]).stat().st_size > 0
