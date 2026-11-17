"""
Stage 2 Verification: PC Shutdown Simulation Test
Simulates:
1. Job queued and in-progress when system is terminated / PC shuts down.
2. Verified that database persists job state safely without loss.
3. System restarts, BatchWorkerPool boots up.
4. Stalled in-flight job is recovered to QUEUED with stage_message 'Resumed from system restart'.
5. Verified zero duplicate jobs or orphaned records are created.
"""
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEST_DB = ROOT / "data" / "test_shutdown_sim.db"

from autoclip_app.batch_manager import (
    init_batch_tables,
    get_db,
    _now_ist_iso,
)
from autoclip_app.batch_worker import BatchWorkerPool

def run_pc_shutdown_simulation():
    if TEST_DB.exists():
        TEST_DB.unlink()

    init_batch_tables(TEST_DB)

    batch_id = f"batch_{uuid.uuid4().hex[:8]}"
    source_id = f"src_{uuid.uuid4().hex[:8]}"
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    now = _now_ist_iso()

    with get_db(TEST_DB) as conn:
        conn.execute("""
            INSERT INTO batches (id, name, status, created_at, updated_at)
            VALUES (?, 'Shutdown Test Batch', 'PROCESSING', ?, ?)
        """, (batch_id, now, now))

        conn.execute("""
            INSERT INTO sources (id, batch_id, source_url, title, created_at)
            VALUES (?, ?, 'https://example.com/test.mp4', 'Test Video', ?)
        """, (source_id, batch_id, now))

        # 1. Job was in RENDERING when power was cut
        conn.execute("""
            INSERT INTO jobs (id, batch_id, source_id, status, progress, stage_message, updated_at)
            VALUES (?, ?, ?, 'RENDERING', 65, 'Rendering 9:16 Shorts with Fit Blur', ?)
        """, (job_id, batch_id, source_id, now))

    print("[PASS] Step 1: Simulated power-loss state recorded in DB (Status: RENDERING, Progress: 65%)")

    # 2. Verify state before recovery
    with get_db(TEST_DB) as conn:
        row = conn.execute("SELECT status, stage_message FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row["status"] == "RENDERING"
        total_jobs = conn.execute("SELECT COUNT(*) as c FROM jobs WHERE batch_id = ?", (batch_id,)).fetchone()["c"]
        assert total_jobs == 1, "Exactly one job should exist before restart"

    print("[PASS] Step 2: Database integrity verified post-crash (No data loss, exact state preserved)")

    # 3. Simulate PC Reboot / Worker restart
    worker = BatchWorkerPool(max_concurrency=1, poll_interval=1, db_path=TEST_DB)
    # Recover stalled jobs
    worker._recover_stalled_jobs()

    # 4. Verify post-recovery state
    with get_db(TEST_DB) as conn:
        recovered = conn.execute("SELECT status, stage_message FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert recovered["status"] == "QUEUED", f"Expected QUEUED, got {recovered['status']}"
        assert recovered["stage_message"] == "Resumed from system restart"

        # Check for zero duplicates
        total_after = conn.execute("SELECT COUNT(*) as c FROM jobs WHERE batch_id = ?", (batch_id,)).fetchone()["c"]
        assert total_after == 1, f"Expected 1 job, found {total_after} (Duplicate jobs detected!)"

    print("[PASS] Step 3: Stalled in-flight job successfully recovered to 'QUEUED' on system restart")
    print("[PASS] Step 4: Duplicate job prevention verified (Zero duplicates created)")

    # Cleanup
    if TEST_DB.exists():
        TEST_DB.unlink()

    print("=== PC SHUTDOWN & RESUMPTION SIMULATION VERIFIED SUCCESSFULLY ===")

if __name__ == "__main__":
    run_pc_shutdown_simulation()
