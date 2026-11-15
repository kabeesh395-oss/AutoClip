"""
Purge all sample batches, jobs, clips, schedules, and temporary outputs
from autoclip.sqlite3, leaving a completely fresh, empty workspace ready for user video uploads.
"""
import os
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "autoclip.sqlite3"
OUTPUTS_DIR = ROOT / "data" / "outputs"
UPLOADS_DIR = ROOT / "data" / "uploads"

def purge_sample_data():
    print(f"Connecting to database: {DB_PATH}")
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON;")

    print("Purging sample records from tables...")
    con.execute("DELETE FROM schedules;")
    con.execute("DELETE FROM clips;")
    con.execute("DELETE FROM jobs;")
    con.execute("DELETE FROM sources;")
    con.execute("DELETE FROM batches;")
    con.execute("DELETE FROM audit_logs;")
    con.commit()
    con.close()
    print("Database tables purged: schedules, clips, jobs, sources, batches, audit_logs are now empty.")

    # Clean outputs directory
    if OUTPUTS_DIR.exists():
        print(f"Cleaning sample output files and folders from {OUTPUTS_DIR}...")
        for item in OUTPUTS_DIR.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            elif item.suffix in [".mp4", ".srt", ".ass", ".jpg", ".png", ".json"]:
                try:
                    item.unlink(missing_ok=True)
                except Exception as e:
                    print(f"Could not delete {item}: {e}")
        print("Outputs directory cleaned.")

    # Clean uploads directory
    if UPLOADS_DIR.exists():
        print(f"Cleaning upload folders from {UPLOADS_DIR}...")
        for item in UPLOADS_DIR.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                try:
                    item.unlink(missing_ok=True)
                except Exception as e:
                    print(f"Could not delete {item}: {e}")
        print("Uploads directory cleaned.")

    print("\n--- ALL SAMPLE DATA REMOVED SUCCESSFULLY ---")
    print("Database is in a pristine, zero-data state, ready for your real video ingest.")

if __name__ == "__main__":
    purge_sample_data()
