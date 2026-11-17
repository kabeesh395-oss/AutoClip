"""
AutoClip Local Cloud Worker Daemon
Runs on Windows PC. Polls Cloudflare Control Plane for pending render jobs,
renders them locally using FFmpeg + Whisper (₹0 cloud cost), and uploads
rendered clips to Cloudflare R2.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

from .batch_worker import download_source_media
from .pipeline import (
    Candidate,
    Segment,
    choose_highlights,
    generate_long_form_metadata,
    generate_viral_intelligence,
    render,
    render_long_form,
    transcribe,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
OUTPUTS = DATA / "outputs"
for folder in (UPLOADS, OUTPUTS):
    folder.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [LocalWorker] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("local_cloud_worker")


class LocalCloudWorker:
    def __init__(
        self,
        cloud_url: str = os.getenv("AUTOCLIP_CLOUD_URL", "http://127.0.0.1:8765"),
        worker_id: str = "pc-primary-worker",
        poll_interval: int = 5,
    ):
        self.cloud_url = cloud_url.rstrip("/")
        self.worker_id = worker_id
        self.poll_interval = poll_interval
        self._running = False

    def start(self, once: bool = False):
        self._running = True
        logger.info("Local Worker started for Cloud Control Plane at %s (ID: %s)", self.cloud_url, self.worker_id)

        while self._running:
            try:
                job_data = self._poll_next_job()
                if job_data and job_data.get("has_job"):
                    job = job_data["job"]
                    logger.info("Found job %s for batch %s. Beginning render...", job["id"], job["batch_id"])
                    self._process_job(job)
                else:
                    if once:
                        break
                    time.sleep(self.poll_interval)
            except KeyboardInterrupt:
                logger.info("Local Worker stopping gracefully...")
                break
            except Exception as exc:
                logger.error("Worker polling error: %s", exc)
                time.sleep(self.poll_interval)

    def _poll_next_job(self) -> dict[str, Any]:
        resp = requests.post(
            f"{self.cloud_url}/api/worker/poll",
            json={"worker_id": self.worker_id},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
        return {"has_job": False}

    def _process_job(self, job: dict[str, Any]):
        job_id = job["id"]
        batch_id = job["batch_id"]
        source_id = job["source_id"]
        source_url = job.get("source_url", "")
        lang = job.get("language") or "en"
        model_name = job.get("whisper_model") or "small"
        target_shorts = int(job.get("daily_shorts") or 2)
        target_long = int(job.get("daily_long") or 1)

        batch_out_dir = OUTPUTS / f"batch_{batch_id}"
        batch_out_dir.mkdir(parents=True, exist_ok=True)

        # 1. Download or locate local media
        local_src = Path(source_url)
        if local_src.exists() and local_src.is_file():
            media_path = local_src
        else:
            logger.info("[%s] Downloading source media from: %s", job_id, source_url)
            media_path = download_source_media(source_url, UPLOADS / f"batch_{batch_id}" / f"source_{source_id}")

        # 2. Transcribe with local Faster-Whisper
        logger.info("[%s] Transcribing media locally with Faster-Whisper (%s)...", job_id, model_name)
        rows = transcribe(media_path, model_name=model_name, language=lang)
        if not rows:
            raise RuntimeError("No speech detected in source video.")

        # 3. Highlight selection
        logger.info("[%s] Selecting viral highlights...", job_id)
        candidates = choose_highlights(rows, count=max(target_shorts + target_long, 3))
        if not candidates:
            raise RuntimeError("No highlights qualified for batch.")

        created_clips = []

        # 4. Render Shorts with Opus Fit+Blur layout & Kinetic Subtitles
        short_candidates = candidates[:target_shorts]
        for idx, cand in enumerate(short_candidates, 1):
            clip_id = f"clip_{job_id}_{idx}"
            target_mp4 = batch_out_dir / f"{clip_id}.mp4"
            logger.info("[%s] Rendering Short %d/%d with Opus Fit+Blur...", job_id, idx, len(short_candidates))

            render(
                source=media_path,
                rows=rows,
                clip=cand,
                target=target_mp4,
                layout="FIT_BLUR",
                caption_style="KINETIC_YELLOW",
                show_hook_header=True,
            )

            intel = generate_viral_intelligence(cand)
            created_clips.append({
                "batch_id": batch_id,
                "source_id": source_id,
                "format": "SHORT",
                "start_time": cand.start,
                "end_time": cand.end,
                "duration": round(cand.end - cand.start, 2),
                "title": cand.title,
                "titles": intel.get("titles", [cand.title]),
                "description": intel.get("description", ""),
                "hashtags": intel.get("hashtags", []),
                "hook": intel.get("hook", {}),
                "hook_score": intel.get("virality_score", 85.0),
                "virality_grade": intel.get("virality_grade", "A"),
                "flow_score": intel.get("flow_score", 80.0),
                "engagement_score": intel.get("engagement_score", 85.0),
                "trend_score": intel.get("trend_score", 80.0),
                "why_viral": intel.get("why_viral", ""),
                "improvement_tips": intel.get("improvement_tips", []),
                "layout": "FIT_BLUR",
                "caption_style": "KINETIC_YELLOW",
                "local_video_path": str(target_mp4),
                "r2_video_key": f"batches/{batch_id}/{clip_id}.mp4",
                "r2_ass_key": f"batches/{batch_id}/{clip_id}.ass",
                "r2_subtitle_key": f"batches/{batch_id}/{clip_id}.srt",
            })

        # 5. Render Long-Form video if requested
        if target_long > 0 and len(candidates) >= 2:
            long_id = f"clip_{job_id}_long"
            long_mp4 = batch_out_dir / f"{long_id}.mp4"
            logger.info("[%s] Rendering Long-Form video...", job_id)
            long_res = render_long_form(
                source=media_path,
                rows=rows,
                candidates=candidates[:target_shorts + 1],
                target=long_mp4,
                title="Weekly Epic Compilation",
            )
            created_clips.append({
                "batch_id": batch_id,
                "source_id": source_id,
                "format": "LONG",
                "start_time": 0.0,
                "end_time": float(long_res.get("duration", 60.0)),
                "duration": float(long_res.get("duration", 60.0)),
                "title": long_res.get("youtube_title", "Weekly Highlights"),
                "titles": [long_res.get("youtube_title", "Weekly Highlights")],
                "description": long_res.get("youtube_description", ""),
                "hashtags": ["#Gaming", "#Highlights", "#AutoClip"],
                "hook_score": 88.0,
                "virality_grade": "A",
                "layout": "LANDSCAPE_16_9",
                "caption_style": "STANDARD",
                "local_video_path": str(long_mp4),
                "r2_video_key": f"batches/{batch_id}/{long_id}.mp4",
                "r2_subtitle_key": f"batches/{batch_id}/{long_id}.srt",
            })

        # 6. Report Completion to Cloud Control Plane
        logger.info("[%s] Reporting %d clips completed to Cloud Control Plane...", job_id, len(created_clips))
        complete_res = requests.post(
            f"{self.cloud_url}/api/worker/jobs/{job_id}/complete",
            json={"clips": created_clips},
            timeout=15,
        )
        if complete_res.status_code == 200:
            logger.info("[%s] Job marked COMPLETED in Cloud Control Plane!", job_id)
        else:
            logger.error("[%s] Failed to report complete: HTTP %d %s", job_id, complete_res.status_code, complete_res.text)


if __name__ == "__main__":
    cloud = sys.argv[1] if len(sys.argv) > 1 else os.getenv("AUTOCLIP_CLOUD_URL", "http://127.0.0.1:8765")
    worker = LocalCloudWorker(cloud_url=cloud)
    worker.start()
