from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SECRETS_FILE = ROOT / "client_secrets.json"
TOKEN_FILE = DATA / "youtube_token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _save_credentials(credentials: Any) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")


def youtube_status() -> dict[str, Any]:
    if not SECRETS_FILE.exists():
        return {
            "connected": False,
            "configured": False,
            "message": "Add your Google OAuth Desktop client file as client_secrets.json (repository root).",
        }
    if not TOKEN_FILE.exists():
        return {"connected": False, "configured": True, "message": "YouTube account not connected yet."}
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        credentials = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            _save_credentials(credentials)
        if credentials.valid:
            return {"connected": True, "configured": True, "message": "YouTube channel connected."}
        return {"connected": False, "configured": True, "message": "YouTube authorization expired. Reconnect."}
    except Exception as exc:
        return {"connected": False, "configured": True, "message": f"Could not read YouTube token: {exc}"}


def connect_youtube(authorization_code: str | None = None, redirect_uri: str | None = None, port: int = 8088) -> dict[str, Any]:
    if not SECRETS_FILE.exists():
        raise RuntimeError(
            "Missing client_secrets.json. Create a Google Cloud OAuth Desktop app client, "
            "enable YouTube Data API v3, and place the downloaded JSON at client_secrets.json (repository root)."
        )
    from google_auth_oauthlib.flow import InstalledAppFlow

    # Handle direct authorization code exchange if user pastes redirected URL or code
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

        flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS_FILE), SCOPES, redirect_uri=red_uri)
        flow.fetch_token(code=clean_code)
        _save_credentials(flow.credentials)
        return youtube_status()

    # Otherwise run local web server to catch OAuth callback
    flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS_FILE), SCOPES)
    try:
        credentials = flow.run_local_server(
            host="127.0.0.1", port=port, open_browser=True, access_type="offline",
            prompt="consent",
        )
    except OSError:
        # Fallback to random available port if specified port is in use
        credentials = flow.run_local_server(
            host="127.0.0.1", port=0, open_browser=True, access_type="offline",
            prompt="consent",
        )
    _save_credentials(credentials)
    return youtube_status()


def upload_public_video(
    video_path: Path,
    title: str,
    description: str,
    tags: list[str] | None = None,
    category_id: str = "22",
) -> dict[str, str]:
    status = youtube_status()
    if not status["connected"]:
        raise RuntimeError(status["message"])

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    credentials = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    safe_title = (title or video_path.stem).strip()[:100] or video_path.stem
    safe_description = (description or "Created with AutoClip AI.")[:5000]
    safe_tags = [str(tag).strip()[:30] for tag in (tags or []) if str(tag).strip()][:30]
    body = {
        "snippet": {
            "title": safe_title,
            "description": safe_description,
            "tags": safe_tags,
            "categoryId": category_id,
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(
        str(video_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True
    )
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    video_id = str(response["id"])
    return {"video_id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}", "privacy_status": "public"}


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

    credentials = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
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
