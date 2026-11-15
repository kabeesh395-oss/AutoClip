import pytest
from pathlib import Path
from autoclip_app.batch_manager import (
    init_batch_tables,
    create_creator,
    get_creator,
    list_creators,
    create_batch,
    get_batch,
    list_batches,
    add_source_to_batch,
    update_job_status,
    set_clip_approval,
    generate_batch_schedule_slots,
    get_batch_report,
    get_db,
    JOB_STATES,
)


@pytest.fixture
def test_db(tmp_path: Path):
    db_file = tmp_path / "test_batch.sqlite3"
    init_batch_tables(db_file)
    return db_file


def test_batch_creation_default_configuration(test_db):
    batch = create_batch("Weekly Esports Highlights", db_path=test_db)
    assert batch["name"] == "Weekly Esports Highlights"
    assert batch["target_videos_per_day"] == 3
    assert batch["schedule_days"] == 3
    assert batch["batch_target"] == 9  # 3 x 3 = 9
    assert batch["daily_shorts"] == 2
    assert batch["daily_long"] == 1
    assert batch["timezone"] == "Asia/Kolkata"
    assert batch["status"] == "DRAFT"


def test_creator_permission_and_rights_blocking(test_db):
    # Register active creator
    creator = create_creator(
        name="ProGamer123",
        channel_url="https://youtube.com/@ProGamer123",
        permission_status="ACTIVE",
        commercial_use_status="ALLOWED",
        db_path=test_db,
    )
    assert creator["id"]
    assert creator["permission_status"] == "ACTIVE"

    batch = create_batch("Batch 1", db_path=test_db)

    # Add source with valid creator
    source1 = add_source_to_batch(
        batch_id=batch["id"],
        source_url="https://youtube.com/watch?v=valid1",
        creator_id=creator["id"],
        db_path=test_db,
    )
    assert source1["rights_status"] == "PASS"

    # Add source with missing / revoked creator
    revoked = create_creator(
        name="RevokedStreamer",
        channel_url="https://youtube.com/@Revoked",
        permission_status="REVOKED",
        db_path=test_db,
    )
    # Mark revoked
    with get_db(test_db) as conn:
        conn.execute("UPDATE creators SET revoked_at = '2026-11-15T12:00:00' WHERE id = ?", (revoked["id"],))

    source_revoked = add_source_to_batch(
        batch_id=batch["id"],
        source_url="https://youtube.com/watch?v=revoked1",
        creator_id=revoked["id"],
        db_path=test_db,
    )
    assert source_revoked["rights_status"] == "BLOCKED"


def test_prevent_duplicate_sources_in_same_batch(test_db):
    batch = create_batch("Batch Dup Test", db_path=test_db)
    add_source_to_batch(batch["id"], "https://youtube.com/watch?v=unique1", db_path=test_db)
    with pytest.raises(ValueError, match="already in this batch"):
        add_source_to_batch(batch["id"], "https://youtube.com/watch?v=unique1", db_path=test_db)


def test_job_state_transitions(test_db):
    batch = create_batch("Job States Batch", db_path=test_db)
    source = add_source_to_batch(batch["id"], "https://youtube.com/watch?v=jobtest", db_path=test_db)

    with get_db(test_db) as conn:
        job = conn.execute("SELECT id FROM jobs WHERE batch_id = ?", (batch["id"],)).fetchone()
        job_id = job["id"]

    # Verify all 15 states are accepted
    for state in JOB_STATES:
        update_job_status(job_id, state, progress=50, stage_message=f"Testing {state}", db_path=test_db)

    # Invalid state must fail
    with pytest.raises(ValueError, match="Invalid job state"):
        update_job_status(job_id, "NON_EXISTENT_STATE", db_path=test_db)


def test_mandatory_manual_approval_and_batch_scheduler(test_db):
    batch = create_batch("Schedule Test Batch", db_path=test_db)
    source = add_source_to_batch(batch["id"], "https://youtube.com/watch?v=scheduletest", db_path=test_db)

    # Insert 6 Shorts and 3 Long mock clips
    with get_db(test_db) as conn:
        for i in range(1, 7):
            conn.execute("""
                INSERT INTO clips (id, batch_id, source_id, format, start_time, end_time, duration,
                                   title, hook_score, approval_status, rights_status, created_at)
                VALUES (?, ?, ?, 'SHORT', 0, 30, 30, ?, 90, 'PENDING', 'PASS', '2026-11-15T00:00:00')
            """, (f"clip_short_{i}", batch["id"], source["id"], f"Viral Short {i}"))

        for i in range(1, 4):
            conn.execute("""
                INSERT INTO clips (id, batch_id, source_id, format, start_time, end_time, duration,
                                   title, hook_score, approval_status, rights_status, created_at)
                VALUES (?, ?, ?, 'LONG', 0, 600, 600, ?, 95, 'PENDING', 'PASS', '2026-11-15T00:00:00')
            """, (f"clip_long_{i}", batch["id"], source["id"], f"Compilation Long Video {i}"))

    # Attempting to schedule unapproved clips must fail
    with pytest.raises(ValueError, match="100% manual approval"):
        generate_batch_schedule_slots(batch["id"], db_path=test_db)

    # Manually approve all 9 clips
    for i in range(1, 7):
        set_clip_approval(f"clip_short_{i}", "APPROVED", db_path=test_db)
    for i in range(1, 4):
        set_clip_approval(f"clip_long_{i}", "APPROVED", db_path=test_db)

    # Schedule approved clips
    scheduled = generate_batch_schedule_slots(batch["id"], start_date_str="2026-11-16", db_path=test_db)
    assert len(scheduled) == 9  # Exactly 9 videos across 3 days

    # Check Day 1 slots (12:00 PM, 4:00 PM, 7:00 PM)
    day1_slots = [s for s in scheduled if s["day"] == "2026-11-16"]
    assert len(day1_slots) == 3
    assert day1_slots[0]["slot_time"] == "12:00 PM" and day1_slots[0]["format"] == "SHORT"
    assert day1_slots[1]["slot_time"] == "04:00 PM" and day1_slots[1]["format"] == "SHORT"
    assert day1_slots[2]["slot_time"] == "07:00 PM" and day1_slots[2]["format"] == "LONG"

    # Batch report
    report = get_batch_report(batch["id"], db_path=test_db)
    assert report["sources_imported"] == 1
    assert report["candidate_clips_generated"] == 9
    assert report["shorts_completed"] == 6
    assert report["long_videos_completed"] == 3
    assert report["videos_approved"] == 9
    assert report["videos_scheduled"] == 9
    assert report["ready_to_publish"] is True
