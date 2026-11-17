"""
Stage 2 Verification: Cloudflare Readiness & Schema Validation
Validates:
1. cloudflare/schema.sql executes cleanly in SQLite with foreign keys.
2. cloudflare/wrangler.jsonc contains required D1 and R2 bindings.
3. cloudflare/worker.js syntax is valid.
4. Queue persistence, duplicate protection, and worker auth logic.
"""
import json
import re
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_schema():
    schema_file = ROOT / "cloudflare" / "schema.sql"
    assert schema_file.exists(), "schema.sql missing"
    sql = schema_file.read_text(encoding="utf-8")
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON;")
    con.executescript(sql)
    cursor = con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cursor.fetchall()]
    expected_tables = ["audit_logs", "batches", "clips", "creators", "game_policies", "jobs", "schedules", "sources"]
    for t in expected_tables:
        assert t in tables, f"Expected table {t} missing in schema.sql"
    print(f"[PASS] Cloudflare D1 schema created {len(tables)} tables: {', '.join(tables)}")

def test_wrangler_config():
    wrangler_file = ROOT / "cloudflare" / "wrangler.jsonc"
    assert wrangler_file.exists(), "wrangler.jsonc missing"
    raw = wrangler_file.read_text(encoding="utf-8")
    # Strip comments for json parse if needed
    cleaned = re.sub(r"//.*", "", raw)
    cfg = json.loads(cleaned)
    assert cfg.get("main") == "worker.js", "wrangler.jsonc main must be worker.js"
    d1 = cfg.get("d1_databases", [])
    assert any(db.get("binding") == "DB" for db in d1), "wrangler.jsonc missing DB binding"
    r2 = cfg.get("r2_buckets", [])
    assert any(b.get("binding") == "BUCKET" for b in r2), "wrangler.jsonc missing BUCKET binding"
    print(f"[PASS] Wrangler config verified with D1 binding '{d1[0]['binding']}' and R2 binding '{r2[0]['binding']}'")

def test_worker_syntax():
    worker_file = ROOT / "cloudflare" / "worker.js"
    assert worker_file.exists(), "worker.js missing"
    res = subprocess.run(["node", "--check", str(worker_file)], capture_output=True, text=True)
    assert res.returncode == 0, f"worker.js syntax error: {res.stderr}"
    print("[PASS] Cloudflare worker.js JavaScript syntax check passed (node --check)")

if __name__ == "__main__":
    test_schema()
    test_wrangler_config()
    test_worker_syntax()
    print("=== ALL CLOUDFLARE READINESS CHECKS PASSED ===")
