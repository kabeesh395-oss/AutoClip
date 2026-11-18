from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ContentItem, PlatformType, PrivacyStatus, ProductionRules, PublishSchedule, PublishStatus
from .storage import DEFAULT_DB, ProductionStorage

logger = logging.getLogger("autoclip.production.publisher")

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
INSTAGRAM_TOKEN_FILE = DATA / "instagram_token.json"


class PublisherError(Exception):
    """Base publisher exception."""
    pass


class RightsConfirmationRequiredError(PublisherError):
    """Raised when publishing is attempted without explicit rights/ownership confirmation."""
    pass


class DuplicateUploadError(PublisherError):
    """Raised when an identical video has already been published or scheduled."""
    pass


class QuotaExceededError(PublisherError):
    """Raised when platform daily upload quota or rate limits are reached."""
    pass


class YouTubePublisher:
    """Publishes or schedules YouTube Shorts (9:16) and YouTube Long-Form (16:9)."""

    def __init__(self) -> None:
        pass

    def check_status(self) -> dict[str, Any]:
        from ..youtube import youtube_status
        return youtube_status()

    def publish(
        self,
        item: ContentItem,
        schedule: PublishSchedule,
        is_scheduled: bool = False,
    ) -> dict[str, Any]:
        from ..youtube import schedule_youtube_video, upload_public_video, youtube_status

        stat = youtube_status()
        if not stat.get("connected"):
            raise PublisherError(f"YouTube not connected: {stat.get('message')}")

        # Choose appropriate video file variant
        if schedule.platform == PlatformType.YOUTUBE_LONGFORM.value:
            vpath_str = item.video_path_horizontal or item.video_path_vertical
        else:
            vpath_str = item.video_path_vertical or item.video_path_horizontal

        if not vpath_str or not Path(vpath_str).exists():
            raise FileNotFoundError(f"Video file not found for item {item.id}: {vpath_str}")

        video_path = Path(vpath_str)

        # Prepare title, description, tags
        title = item.title or "Viral Highlight"
        if schedule.platform == PlatformType.YOUTUBE_SHORTS.value and "#shorts" not in title.lower():
            # Ensure title or tags indicate Shorts
            pass

        description = item.description or ""
        tags = item.hashtags or ["#shorts", "#viral"]

        try:
            if is_scheduled:
                res = schedule_youtube_video(
                    video_path=video_path,
                    title=title,
                    description=description,
                    publish_at_iso=schedule.scheduled_time_iso,
                    tags=tags,
                )
            else:
                res = upload_public_video(
                    video_path=video_path,
                    title=title,
                    description=description,
                    tags=tags,
                    privacy_status=schedule.privacy_status,
                )
            return {
                "external_id": res.get("video_id"),
                "external_url": res.get("url"),
                "status": "published",
                "raw_response": res,
            }
        except Exception as exc:
            err_str = str(exc)
            if "quotaExceeded" in err_str or "uploadLimitExceeded" in err_str:
                logger.error("YouTube quota exceeded: %s", err_str)
                raise QuotaExceededError(f"YouTube upload quota reached: {err_str}")
            raise PublisherError(f"YouTube upload failed: {err_str}")


class InstagramReelsPublisher:
    """
    Publishes Instagram Reels via official Meta Graph API (v19.0+).
    Requires an Instagram Business or Creator account connected to a Facebook Page.
    """

    def __init__(self, token_file: Path = INSTAGRAM_TOKEN_FILE) -> None:
        self.token_file = token_file

    def get_credentials(self) -> dict[str, str]:
        # Check environment variables first
        access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
        user_id = os.getenv("INSTAGRAM_USER_ID")

        if access_token and user_id:
            return {"access_token": access_token.strip(), "user_id": user_id.strip()}

        if self.token_file.exists():
            try:
                data = json.loads(self.token_file.read_text(encoding="utf-8"))
                if data.get("access_token") and data.get("user_id"):
                    return {
                        "access_token": data["access_token"].strip(),
                        "user_id": data["user_id"].strip(),
                    }
            except Exception:
                pass
        return {}

    def save_credentials(self, access_token: str, user_id: str) -> None:
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(
            json.dumps({"access_token": access_token.strip(), "user_id": user_id.strip()}, indent=2),
            encoding="utf-8"
        )

    def check_status(self) -> dict[str, Any]:
        creds = self.get_credentials()
        if not creds:
            return {
                "connected": False,
                "configured": False,
                "account_type": None,
                "username": None,
                "message": (
                    "Instagram Graph API not connected. Provide Meta Graph API user_id and access_token "
                    "with 'instagram_basic' and 'instagram_content_publish' permissions for an Instagram Business/Creator account."
                ),
            }

        user_id = creds["user_id"]
        token = creds["access_token"]
        url = f"https://graph.facebook.com/v19.0/{user_id}?fields=id,username,account_type&access_token={token}"

        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                acc_type = data.get("account_type", "").upper()
                username = data.get("username", "Unknown")

                if acc_type not in ("BUSINESS", "MEDIA_CREATOR", "CREATOR"):
                    return {
                        "connected": False,
                        "configured": True,
                        "account_type": acc_type,
                        "username": username,
                        "message": f"Account '{username}' is {acc_type}. Reels publishing requires Instagram Business or Creator account.",
                    }

                return {
                    "connected": True,
                    "configured": True,
                    "account_type": acc_type,
                    "username": username,
                    "message": f"Connected to Instagram {acc_type} account (@{username}).",
                }
        except Exception as exc:
            return {
                "connected": False,
                "configured": True,
                "account_type": None,
                "username": None,
                "message": f"Meta Graph API verification failed: {exc}",
            }

    def publish(
        self,
        item: ContentItem,
        schedule: PublishSchedule,
        public_base_url: str = "http://localhost:8765",
    ) -> dict[str, Any]:
        """
        Executes Meta Graph API Reel publishing workflow:
        1. Initialize container (POST /{user_id}/media with media_type=REELS)
        2. Poll container status until FINISHED
        3. Publish container (POST /{user_id}/media_publish)
        """
        creds = self.get_credentials()
        if not creds:
            raise PublisherError("Instagram Graph API credentials missing. Configure user_id and access_token.")

        user_id = creds["user_id"]
        token = creds["access_token"]

        vpath_str = item.video_path_vertical or item.video_path_horizontal
        if not vpath_str or not Path(vpath_str).exists():
            raise FileNotFoundError(f"Video file not found: {vpath_str}")

        video_path = Path(vpath_str)
        # Determine public URL for Meta servers to download video
        # In production/deployment, this is a publicly accessible URL
        rel_path = video_path.name
        video_url = f"{public_base_url}/outputs/{rel_path}"

        caption = f"{item.title}\n\n{item.description}\n\n{' '.join(item.hashtags)}"

        # 1. Initialize Reel media container
        init_url = f"https://graph.facebook.com/v19.0/{user_id}/media"
        init_params = urllib.parse.urlencode({
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption[:2200],
            "access_token": token,
        }).encode("utf-8")

        try:
            req = urllib.request.Request(init_url, data=init_params, method="POST")
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                init_res = json.loads(resp.read().decode("utf-8"))
                container_id = init_res.get("id")
                if not container_id:
                    raise PublisherError(f"Failed to create Reel container: {init_res}")
        except urllib.error.HTTPError as h_err:
            body = h_err.read().decode("utf-8", errors="ignore")
            if "OAuthRateLimitException" in body:
                raise QuotaExceededError(f"Meta Graph API rate limit reached: {body}")
            raise PublisherError(f"Instagram container creation error ({h_err.code}): {body}")

        # 2. Poll container status (max 60s)
        status_url = f"https://graph.facebook.com/v19.0/{container_id}?fields=status_code,status&access_token={token}"
        is_ready = False
        for _ in range(12):
            time.sleep(5)
            try:
                with urllib.request.urlopen(urllib.request.Request(status_url), timeout=5.0) as resp:
                    st_data = json.loads(resp.read().decode("utf-8"))
                    code = st_data.get("status_code")
                    if code == "FINISHED":
                        is_ready = True
                        break
                    elif code == "ERROR":
                        raise PublisherError(f"Instagram media processing failed: {st_data}")
            except Exception as e:
                logger.warning("Error checking container status: %s", e)

        if not is_ready:
            raise PublisherError("Instagram video processing timed out before finishing.")

        # 3. Publish container
        pub_url = f"https://graph.facebook.com/v19.0/{user_id}/media_publish"
        pub_params = urllib.parse.urlencode({
            "creation_id": container_id,
            "access_token": token,
        }).encode("utf-8")

        try:
            req = urllib.request.Request(pub_url, data=pub_params, method="POST")
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                pub_res = json.loads(resp.read().decode("utf-8"))
                media_id = pub_res.get("id")
                return {
                    "external_id": media_id,
                    "external_url": f"https://www.instagram.com/reel/{media_id}/",
                    "status": "published",
                    "raw_response": pub_res,
                }
        except Exception as exc:
            raise PublisherError(f"Instagram publish container error: {exc}")


class MultiPublisherRouter:
    """
    Coordinates multi-platform publishing, enforcing safety gates,
    rights confirmation, deduplication, and quota handling.
    """

    def __init__(self, storage: ProductionStorage | None = None) -> None:
        self.storage = storage or ProductionStorage()
        self.yt_publisher = YouTubePublisher()
        self.ig_publisher = InstagramReelsPublisher()

    def get_platforms_status(self) -> dict[str, Any]:
        return {
            "youtube": self.yt_publisher.check_status(),
            "instagram": self.ig_publisher.check_status(),
        }

    def publish_schedule(self, schedule_id: str, public_base_url: str = "http://localhost:8765") -> dict[str, Any]:
        sched = self.storage.get_schedule(schedule_id)
        if not sched:
            raise PublisherError(f"Schedule {schedule_id} not found.")

        item = self.storage.get_content_item(sched.item_id)
        if not item:
            raise PublisherError(f"Content item {sched.item_id} not found.")

        rules = self.storage.get_rules()

        # 1. Rights Confirmation Gate
        if rules.require_rights_confirmation and not sched.rights_confirmed:
            sched.status = PublishStatus.FAILED.value
            sched.error_message = "Publishing blocked: Rights & Ownership confirmation required."
            self.storage.update_schedule(sched)
            raise RightsConfirmationRequiredError(sched.error_message)

        # 2. Quality Score Gate
        if item.dialogue_quality_score < rules.min_quality_score:
            sched.status = PublishStatus.FAILED.value
            sched.error_message = (
                f"Quality score {item.dialogue_quality_score:.1f} is below minimum threshold {rules.min_quality_score:.1f}."
            )
            self.storage.update_schedule(sched)
            raise PublisherError(sched.error_message)

        # 3. Duplicate Prevention via SHA256 Fingerprint
        if item.sha256_hash and self.storage.is_hash_published(item.sha256_hash, sched.platform, exclude_schedule_id=sched.id):
            sched.status = PublishStatus.FAILED.value
            sched.error_message = "Duplicate detected: this video hash has already been published/scheduled."
            self.storage.update_schedule(sched)
            raise DuplicateUploadError(sched.error_message)

        # 4. Daily Publishing Limit Enforcement
        published_today = self.storage.count_published_today(sched.platform)
        limit = {
            PlatformType.YOUTUBE_SHORTS.value: rules.daily_limit_shorts,
            PlatformType.YOUTUBE_LONGFORM.value: rules.daily_limit_longform,
            PlatformType.INSTAGRAM_REELS.value: rules.daily_limit_reels,
        }.get(sched.platform, 3)

        if published_today >= limit:
            sched.status = PublishStatus.FAILED.value
            sched.error_message = f"Daily publish limit reached for {sched.platform} ({published_today}/{limit})."
            self.storage.update_schedule(sched)
            raise QuotaExceededError(sched.error_message)

        # Mark as uploading
        sched.status = PublishStatus.UPLOADING.value
        self.storage.update_schedule(sched)

        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            is_scheduled_future = sched.scheduled_time_iso > now_iso

            if sched.platform in (PlatformType.YOUTUBE_SHORTS.value, PlatformType.YOUTUBE_LONGFORM.value):
                res = self.yt_publisher.publish(item, sched, is_scheduled=is_scheduled_future)
            elif sched.platform == PlatformType.INSTAGRAM_REELS.value:
                res = self.ig_publisher.publish(item, sched, public_base_url=public_base_url)
            else:
                raise PublisherError(f"Unsupported platform: {sched.platform}")

            # Success
            sched.status = PublishStatus.PUBLISHED.value
            sched.external_id = res.get("external_id")
            sched.external_url = res.get("external_url")
            sched.published_at = datetime.now(timezone.utc).isoformat()
            sched.error_message = None
            self.storage.update_schedule(sched)
            return res

        except QuotaExceededError as q_err:
            sched.status = PublishStatus.FAILED.value
            sched.retry_count += 1
            sched.error_message = f"Quota error: {q_err}"
            self.storage.update_schedule(sched)
            raise

        except Exception as exc:
            sched.status = PublishStatus.FAILED.value
            sched.retry_count += 1
            sched.error_message = str(exc)
            self.storage.update_schedule(sched)
            raise
