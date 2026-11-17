import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google_auth_oauthlib.flow import InstalledAppFlow

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

ROOT = Path("AutoClip").resolve()
DATA = ROOT / "data"
DATA.mkdir(parents=True, exist_ok=True)
SECRETS_FILE = ROOT / "client_secrets.json"
TOKEN_FILE = DATA / "youtube_token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

PORT = 8088
REDIRECT_URI = f"http://localhost:{PORT}/"

flow = InstalledAppFlow.from_client_secrets_file(
    str(SECRETS_FILE),
    SCOPES,
    redirect_uri=REDIRECT_URI
)

auth_url, state = flow.authorization_url(
    access_type="offline",
    prompt="consent"
)

auth_state_path = DATA / "oauth_flow_state.json"
auth_state_path.write_text(json.dumps({"state": state, "redirect_uri": REDIRECT_URI}), encoding="utf-8")

print("\n" + "=" * 60, flush=True)
print("CLICK THIS URL TO CONNECT YOUTUBE:", flush=True)
print(auth_url, flush=True)
print("=" * 60 + "\n", flush=True)

class OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = urlparse(self.path).query
        params = parse_qs(query)
        
        if "code" in params:
            code = params["code"][0]
            try:
                flow.fetch_token(code=code)
                credentials = flow.credentials
                TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
                
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<h1>YouTube Connected Successfully!</h1><p>You can close this tab now and return to Antigravity.</p>")
                print("\n>>> SUCCESS: YouTube Token saved to data/youtube_token.json! <<<\n", flush=True)
                
                # Exit server after successful auth
                import threading
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(f"<h1>Error: {e}</h1>".encode("utf-8"))
                print(f"Error fetching token: {e}", flush=True)
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing code parameter.")

    def log_message(self, format, *args):
        # Silence standard HTTP access logs
        pass

server = HTTPServer(("127.0.0.1", PORT), OAuthHandler)
print(f"Listening on http://localhost:{PORT} for Google OAuth redirect...", flush=True)
server.serve_forever()
print("Server stopped cleanly.", flush=True)
