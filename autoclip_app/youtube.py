from __future__ import annotations

import json
import logging
import os
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SECRETS_FILE = ROOT / "client_secrets.json"
TOKEN_FILE = DATA / "youtube_token.json"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

_ACTIVE_FLOW: Any = None
_LISTENER_SERVER: ThreadingHTTPServer | None = None
_LISTENER_THREAD: threading.Thread | None = None
logger = logging.getLogger("autoclip.youtube")


def _save_credentials(credentials: Any) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")


def _get_token_scopes() -> list[str]:
    if TOKEN_FILE.exists():
        try:
            with TOKEN_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
                scopes = data.get("scopes")
                if isinstance(scopes, list) and scopes:
                    return scopes
        except Exception:
            pass
    return SCOPES


def youtube_status() -> dict[str, Any]:
    if not SECRETS_FILE.exists():
        return {
            "connected": False,
            "configured": False,
            "channel_title": None,
            "channel_thumbnail": None,
            "message": "Add your Google OAuth Desktop client file as client_secrets.json (repository root).",
        }
    if not TOKEN_FILE.exists():
        return {
            "connected": False,
            "configured": True,
            "channel_title": None,
            "channel_thumbnail": None,
            "message": "YouTube account not connected yet. Click Connect to link your channel.",
        }
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        credentials = Credentials.from_authorized_user_file(str(TOKEN_FILE), _get_token_scopes())
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            _save_credentials(credentials)

        if credentials.valid:
            channel_name = None
            channel_thumb = None
            try:
                from googleapiclient.discovery import build
                yt = build("youtube", "v3", credentials=credentials, cache_discovery=False)
                res = yt.channels().list(part="snippet", mine=True).execute()
                items = res.get("items", [])
                if items:
                    channel_name = items[0]["snippet"].get("title")
                    channel_thumb = items[0]["snippet"].get("thumbnails", {}).get("default", {}).get("url")
            except Exception:
                pass

            return {
                "connected": True,
                "configured": True,
                "channel_title": channel_name or "Connected Channel (Upload Ready)",
                "channel_thumbnail": channel_thumb,
                "message": f"YouTube connected ({channel_name or 'Upload permissions active'}).",
            }
        return {
            "connected": False,
            "configured": True,
            "channel_title": None,
            "channel_thumbnail": None,
            "message": "YouTube authorization expired. Reconnect.",
        }
    except Exception as exc:
        return {
            "connected": False,
            "configured": True,
            "channel_title": None,
            "channel_thumbnail": None,
            "message": f"Could not read YouTube token: {exc}",
        }


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        global _ACTIVE_FLOW
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]

        if code and _ACTIVE_FLOW:
            try:
                _ACTIVE_FLOW.fetch_token(code=code)
                _save_credentials(_ACTIVE_FLOW.credentials)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                html = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>YouTube Connected - AutoClip</title>
  <style>
    body { background: #0B0F17; color: #F1F5F9; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
    .card { background: #162032; border: 1px solid #10B981; border-radius: 20px; padding: 40px; text-align: center; max-width: 420px; box-shadow: 0 20px 40px rgba(0,0,0,0.5); }
    h1 { color: #10B981; margin: 0 0 12px 0; font-size: 24px; }
    p { color: #94A3B8; font-size: 14px; line-height: 1.5; margin: 0 0 20px 0; }
    .btn { display: inline-block; background: #8B5CF6; color: white; padding: 12px 24px; border-radius: 12px; text-decoration: none; font-weight: 600; font-size: 14px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>&#10004; Channel Connected!</h1>
    <p>Your YouTube account is successfully connected to AutoClip Studio. You can now close this window and return to publish or schedule your clips.</p>
    <a href="javascript:window.close()" class="btn">Close Window</a>
  </div>
</body>
</html>"""
                self.wfile.write(html.encode("utf-8"))
                return
            except Exception as e:
                logger.error("Failed to exchange token in callback: %s", e)
                self.send_response(400)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(f"OAuth token exchange error: {e}".encode("utf-8"))
                return

        # If accessed without code (e.g. ping or favicon)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"AutoClip OAuth Listener Ready")

    def log_message(self, format: str, *args: Any) -> None:
        pass


def _start_listener_background(port: int = 8088) -> None:
    global _LISTENER_SERVER, _LISTENER_THREAD
    if _LISTENER_SERVER is not None:
        return

    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), _OAuthCallbackHandler)
        _LISTENER_SERVER = server

        def serve():
            try:
                server.serve_forever()
            except Exception:
                pass

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        _LISTENER_THREAD = thread
    except Exception as exc:
        logger.warning("Could not bind OAuth listener on port %s: %s", port, exc)


def get_authorization_url(redirect_uri: str = "http://127.0.0.1:8088/") -> str:
    global _ACTIVE_FLOW
    if not SECRETS_FILE.exists():
        raise RuntimeError("Missing client_secrets.json at repository root.")

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        str(SECRETS_FILE), SCOPES, redirect_uri=redirect_uri
    )
    auth_url, _ = flow.authorization_url(
        prompt="consent",
        access_type="offline",
        include_granted_scopes="true",
    )
    _ACTIVE_FLOW = flow
    _start_listener_background(port=8088)
    return auth_url


def connect_youtube(
    authorization_code: str | None = None,
    redirect_uri: str | None = None,
    port: int = 8088,
) -> dict[str, Any]:
    global _ACTIVE_FLOW
    if not SECRETS_FILE.exists():
        raise RuntimeError(
            "Missing client_secrets.json. Create a Google Cloud OAuth Desktop app client, "
            "enable YouTube Data API v3, and place the downloaded JSON at client_secrets.json (repository root)."
        )

    # Direct authorization code exchange
    if authorization_code:
        import urllib.parse

        clean_code = authorization_code.strip()
        red_uri = redirect_uri or "http://127.0.0.1:8088/"
        if "code=" in clean_code:
            parsed = urllib.parse.urlparse(clean_code)
            params = urllib.parse.parse_qs(parsed.query)
            clean_code = params.get("code", [clean_code])[0]
            if not redirect_uri and parsed.netloc:
                red_uri = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

        flow = _ACTIVE_FLOW
        if flow is None:
            from google_auth_oauthlib.flow import InstalledAppFlow

            flow = InstalledAppFlow.from_client_secrets_file(
                str(SECRETS_FILE), SCOPES, redirect_uri=red_uri
            )

        flow.fetch_token(code=clean_code)
        _save_credentials(flow.credentials)
        _ACTIVE_FLOW = None
        return youtube_status()

    # Otherwise return authorization URL and activate background listener
    auth_url = get_authorization_url(redirect_uri=redirect_uri or f"http://127.0.0.1:{port}/")
    stat = youtube_status()
    stat["auth_url"] = auth_url
    return stat


def upload_public_video(
    video_path: Path,
    title: str,
    description: str,
    tags: list[str] | None = None,
    category_id: str = "22",
    privacy_status: str = "public",
) -> dict[str, str]:
    status = youtube_status()
    if not status["connected"]:
        raise RuntimeError(status["message"])

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    credentials = Credentials.from_authorized_user_file(str(TOKEN_FILE), _get_token_scopes())
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    safe_title = (title or video_path.stem).strip()[:100] or video_path.stem
    safe_description = (description or "Created with AutoClip AI.")[:5000]
    safe_tags = [str(tag).strip()[:30] for tag in (tags or []) if str(tag).strip()][:30]

    privacy = privacy_status if privacy_status in ["public", "unlisted", "private"] else "public"
    body = {
        "snippet": {
            "title": safe_title,
            "description": safe_description,
            "tags": safe_tags,
            "categoryId": category_id,
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(
        str(video_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True
    )
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    video_id = str(response["id"])
    return {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "privacy_status": privacy,
    }


def schedule_youtube_video(
    video_path: Path,
    title: str,
    description: str,
    publish_at_iso: str,
    tags: list[str] | None = None,
    category_id: str = "22",
) -> dict[str, str]:
    """
    Upload a video to YouTube with scheduled publishing.
    Video is uploaded as 'private' with 'publishAt' timestamp in UTC/ISO8601.
    YouTube automatically publishes the video at the designated time, allowing the
    local computer to be shut down safely.
    """
    status = youtube_status()
    if not status["connected"]:
        raise RuntimeError(status["message"])

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    credentials = Credentials.from_authorized_user_file(str(TOKEN_FILE), _get_token_scopes())
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    safe_title = (title or video_path.stem).strip()[:100] or video_path.stem
    safe_description = (description or "Created with AutoClip AI.")[:5000]
    safe_tags = [str(tag).strip()[:30] for tag in (tags or []) if str(tag).strip()][:30]

    try:
        dt = datetime.fromisoformat(publish_at_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
        utc_dt = dt.astimezone(timezone.utc)
        publish_at_utc = utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        publish_at_utc = publish_at_iso

    body = {
        "snippet": {
            "title": safe_title,
            "description": safe_description,
            "tags": safe_tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at_utc,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(
        str(video_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True
    )
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    video_id = str(response["id"])
    return {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "privacy_status": "private",
        "publish_at": publish_at_utc,
    }
