import os
import sys
import traceback
from pathlib import Path

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

log_path = root / "data" / "auth_debug.log"
log_path.parent.mkdir(parents=True, exist_ok=True)

def log(msg):
    print(msg, flush=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(str(msg) + "\n")

log("Starting YouTube OAuth authorization flow...")
try:
    from autoclip_app.youtube import connect_youtube
    status = connect_youtube()
    log("SUCCESS: YouTube account successfully connected!")
    log(str(status))
except Exception as e:
    log(f"ERROR: {e}")
    log(traceback.format_exc())
    sys.exit(1)
