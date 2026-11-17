import sqlite3
from pathlib import Path

for name in ["autoclip.sqlite3", "autoclip_batch.db"]:
    p = Path("AutoClip/data") / name
    if p.exists():
        con = sqlite3.connect(p)
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        print(f"=== {name} ===")
        print("Tables:", tables)
        for t in tables:
            try:
                cnt = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                print(f"  {t}: {cnt} rows")
            except Exception as e:
                print(f"  {t}: {e}")
