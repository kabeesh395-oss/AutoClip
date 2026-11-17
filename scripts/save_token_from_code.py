import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google_auth_oauthlib.flow import InstalledAppFlow

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

ROOT = Path("AutoClip").resolve()
DATA = ROOT / "data"
SECRETS_FILE = ROOT / "client_secrets.json"
TOKEN_FILE = DATA / "youtube_token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

if len(sys.argv) < 2:
    print("Usage: python save_token_from_code.py <code_or_full_url>")
    sys.exit(1)

raw_input = sys.argv[1].strip()

# Check if full URL or just code
if "code=" in raw_input:
    parsed = urlparse(raw_input)
    params = parse_qs(parsed.query)
    code = params.get("code", [raw_input])[0]
else:
    code = raw_input

auth_state_path = DATA / "oauth_flow_state.json"
redirect_uri = "http://localhost:8088/"
if auth_state_path.exists():
    try:
        saved_state = json.loads(auth_state_path.read_text(encoding="utf-8"))
        redirect_uri = saved_state.get("redirect_uri", redirect_uri)
    except Exception:
        pass

flow = InstalledAppFlow.from_client_secrets_file(
    str(SECRETS_FILE),
    SCOPES,
    redirect_uri=redirect_uri
)

flow.fetch_token(code=code)
credentials = flow.credentials
TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
print("SUCCESS: YouTube Token saved to data/youtube_token.json!")
