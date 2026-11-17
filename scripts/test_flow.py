import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

ROOT = Path("AutoClip")
SECRETS_FILE = ROOT / "client_secrets.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

flow = InstalledAppFlow.from_client_secrets_file(
    str(SECRETS_FILE),
    SCOPES,
    redirect_uri="http://localhost:8088/"
)

auth_url, state = flow.authorization_url(
    access_type="offline",
    prompt="consent"
)

print("AUTH_URL:", auth_url)
print("STATE:", state)
