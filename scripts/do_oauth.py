import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from autoclip_app.youtube import connect_youtube

print("--- STARTING YOUTUBE OAUTH FLOW ---")
try:
    res = connect_youtube()
    print("--- OAUTH SUCCEEDED ---")
    print("Status:", res)
except Exception as e:
    print("--- OAUTH FAILED ---")
    print("Error:", e)
