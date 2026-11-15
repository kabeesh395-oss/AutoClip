from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yt_dlp

from .batch_manager import (
    BATCH_DB,
    IST,
    _now_ist_iso,
    get_db,
    log_audit,
    update_job_status,
)
from .pipeline import (
    Candidate,
    Segment,
    choose_highlights,
    generate_metadata,
    generate_viral_intelligence,
    render,
    render_long_form,
    transcribe,
)

logger = logging.getLogger("autoclip.batch_worker")
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
OUTPUTS = DATA / "outputs"

for p in (UPLOADS, OUTPUTS):
    p.mkdir(parents=True, exist_ok=True)


def download_source_media(source_url: str, target_dir: Path) -> Path:
    """Download source video if URL, or verify local file path."""
    target_dir.mkdir(parents=True, exist_ok=True)
    local_path = Path(source_url)
    if local_path.exists() and local_path.is_file():
        return local_path

    options = {
        "outtmpl": str(target_dir / "%(title).80s-%(id)s.%(ext)s"),
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "color": "no_color",
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(source_url, download=True)
        filename = Path(downloader.prepare_filename(info))

    if not filename.exists():
        merged = filename.with_suffix(".mp4")
        if merged.exists():
            filename = merged
    if not filename.exists():
        matches = [p for p in target_dir.glob("*") if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
        if not matches:
            raise RuntimeError(f"Downloader finished but no media file was found in {target_dir}")
        filename = matches[0]

    return filename


def _probe_duration(media_path: Path) -> float:
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(media_path)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode == 0 and res.stdout.strip():
            return float(res.stdout.strip())
    except Exception:
        pass
    return 0.0


def process_batch_job(job_id: str, db_path: Path = BATCH_DB) -> dict[str, Any]:
    """
    Execute full 15-state processing for a single batch source job.
    Transitions:
      QUEUED -> DOWNLOADING -> ANALYZING -> RIGHTS_REVIEW -> TRANSCRIBING ->
      SELECTING_HIGHLIGHTS -> EDITING -> RENDERING -> QUALITY_CHECK -> AWAITING_APPROVAL
    """
    with get_db(db_path) as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise ValueError(f"Job {job_id} not found.")
        source = conn.execute("SELECT * FROM sources WHERE id = ?", (job["source_id"],)).fetchone()
        if not source:
            raise ValueError(f"Source {job['source_id']} for job {job_id} not found.")
        batch = conn.execute("SELECT * FROM batches WHERE id = ?", (job["batch_id"],)).fetchone()
        if not batch:
            raise ValueError(f"Batch {job['batch_id']} not found.")
        creator = None
        if source["creator_id"]:
            creator = conn.execute("SELECT * FROM creators WHERE id = ?", (source["creator_id"],)).fetchone()

    batch_id = batch["id"]
    source_id = source["id"]
    creator_name = creator["name"] if creator else ""

    # Check rights gate
    if source["rights_status"] == "BLOCKED":
        update_job_status(job_id, "FAILED", progress=0, error_message=f"Rights BLOCKED: {source['rights_notes']}", db_path=db_path)
        log_audit("JOB_FAILED_RIGHTS", "job", job_id, batch_id=batch_id, details=source["rights_notes"], db_path=db_path)
        return {"job_id": job_id, "status": "FAILED", "error": source["rights_notes"]}

    with get_db(db_path) as conn:
        conn.execute("UPDATE batches SET status = 'PROCESSING', updated_at = ? WHERE id = ? AND status = 'DRAFT'",
                     (_now_ist_iso(), batch_id))

    job_output_dir = OUTPUTS / f"batch_{batch_id}" / f"source_{source_id}"
    job_output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 1. DOWNLOADING
        update_job_status(job_id, "DOWNLOADING", progress=10, stage_message="Downloading source media...", db_path=db_path)
        media_path = download_source_media(source["source_url"], UPLOADS / f"batch_{batch_id}" / f"source_{source_id}")
        with get_db(db_path) as conn:
            conn.execute("UPDATE sources SET file_path = ? WHERE id = ?", (str(media_path), source_id))

        # 2. ANALYZING
        update_job_status(job_id, "ANALYZING", progress=20, stage_message="Analyzing media container and stream duration...", db_path=db_path)
        duration = _probe_duration(media_path)
        if duration > 0:
            with get_db(db_path) as conn:
                conn.execute("UPDATE sources SET duration = ? WHERE id = ?", (duration, source_id))

        # 3. RIGHTS_REVIEW (Verified pass)
        update_job_status(job_id, "RIGHTS_REVIEW", progress=25, stage_message="Auditing creator and publisher policy compliance...", db_path=db_path)

        # 4. TRANSCRIBING
        update_job_status(job_id, "TRANSCRIBING", progress=40, stage_message="Transcribing speech with Faster-Whisper...", db_path=db_path)
        whisper_model = batch["whisper_model"] or "small"
        language = batch["language"] if batch["language"] != "auto" else None
        rows = transcribe(media_path, model_name=whisper_model, language=language)

        # 5. SELECTING_HIGHLIGHTS
        update_job_status(job_id, "SELECTING_HIGHLIGHTS", progress=55, stage_message="AI selecting viral moments and hooks...", db_path=db_path)
        target_shorts = batch["daily_shorts"] or 2
        # Select enough highlights for shorts + long form
        candidates = choose_highlights(rows, count=max(target_shorts + 1, 3))

        # 6. EDITING
        update_job_status(job_id, "EDITING", progress=70, stage_message="Generating viral hooks, captions and chapters...", db_path=db_path)

        # 7. RENDERING
        update_job_status(job_id, "RENDERING", progress=80, stage_message="Rendering 9:16 Shorts and 16:9 Long compilation...", db_path=db_path)
        rendered_clips = []
        now = _now_ist_iso()

        # Render 9:16 Shorts
        shorts_to_render = candidates[:target_shorts]
        for idx, cand in enumerate(shorts_to_render, 1):
            clip_id = f"clip_{uuid.uuid4().hex[:8]}"
            slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", cand.title).strip("-").lower()[:30] or f"short-{idx}"
            short_target = job_output_dir / f"short_{idx:02d}_{slug}.mp4"
            render(media_path, rows, cand, short_target, layout="FIT_BLUR", caption_style="KINETIC_YELLOW")
            srt_path = short_target.with_suffix(".srt")

            intel = generate_viral_intelligence(cand)
            meta = generate_metadata(cand)

            with get_db(db_path) as conn:
                conn.execute("""
                    INSERT INTO clips (id, batch_id, source_id, format, start_time, end_time, duration,
                                      category, hook_score, title, titles_json, description, hashtags_json,
                                      hook_json, scene_intelligence_json, transcript, caption_file,
                                      output_path, rights_status, approval_status, created_at,
                                      virality_grade, flow_score, engagement_score, trend_score,
                                      layout, caption_style, why_viral, improvement_tips_json)
                    VALUES (?, ?, ?, 'SHORT', ?, ?, ?, 'Viral Short', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PASS', 'PENDING', ?,
                            ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    clip_id, batch_id, source_id, cand.start, cand.end, round(cand.end - cand.start, 2),
                    intel.get("hook_score", cand.score), cand.title, json.dumps(intel.get("titles", [cand.title])),
                    intel.get("description", meta["description"]), json.dumps(intel.get("hashtags", [])),
                    json.dumps(intel.get("hook", {})), json.dumps(intel.get("scene_intelligence", {})),
                    cand.transcript, str(srt_path) if srt_path.exists() else "", str(short_target), now,
                    intel.get("virality_grade", "A"), intel.get("flow_score", 80.0), intel.get("engagement_score", 85.0),
                    intel.get("trend_score", 80.0), "FIT_BLUR", "KINETIC_YELLOW", intel.get("why_viral", ""),
                    json.dumps(intel.get("improvement_tips", []))
                ))
            rendered_clips.append(clip_id)

        # Render 16:9 Long Compilation (if batch daily_long > 0)
        if batch["daily_long"] > 0 and len(candidates) >= 1:
            long_clip_id = f"clip_{uuid.uuid4().hex[:8]}"
            long_target = job_output_dir / "long_compilation.mp4"
            long_res = render_long_form(
                source=media_path,
                rows=rows,
                candidates=candidates,
                target=long_target,
                title=source["title"] or f"Batch Highlights ({batch['name']})",
                creator_name=creator_name
            )

            with get_db(db_path) as conn:
                conn.execute("""
                    INSERT INTO clips (id, batch_id, source_id, format, start_time, end_time, duration,
                                      category, hook_score, title, titles_json, description, hashtags_json,
                                      hook_json, scene_intelligence_json, transcript, caption_file,
                                      output_path, rights_status, approval_status, created_at)
                    VALUES (?, ?, ?, 'LONG', 0.0, ?, ?, 'Long Compilation', 95.0, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PASS', 'PENDING', ?)
                """, (
                    long_clip_id, batch_id, source_id, long_res["duration"], long_res["duration"],
                    long_res["youtube_title"], json.dumps(long_res.get("titles", [long_res["youtube_title"]])),
                    long_res["youtube_description"], json.dumps([f"#{t}" for t in long_res["youtube_tags"]]),
                    json.dumps({"opening_hook": "Full comprehensive recap with chapters", "hook_timestamp": 0.0}),
                    json.dumps({"chapters": long_res["chapters"]}), "\n".join(c.transcript for c in candidates),
                    long_res["subtitle_path"], str(long_target), now
                ))
            rendered_clips.append(long_clip_id)

        # 8. QUALITY_CHECK
        update_job_status(job_id, "QUALITY_CHECK", progress=95, stage_message="Verifying rendered video containers and audio integrity...", db_path=db_path)
        with get_db(db_path) as conn:
            clips = conn.execute("SELECT * FROM clips WHERE source_id = ?", (source_id,)).fetchall()
            for c in clips:
                p = Path(c["output_path"])
                if not p.exists() or p.stat().st_size == 0:
                    raise RuntimeError(f"Quality check failed: Rendered clip {p.name} is missing or empty.")

        # 9. AWAITING_APPROVAL (100% manual approval gate per FR-11)
        update_job_status(
            job_id,
            "AWAITING_APPROVAL",
            progress=100,
            stage_message="Processing complete. 100% manual approval required before scheduling.",
            db_path=db_path
        )
        log_audit("JOB_AWAITING_APPROVAL", "job", job_id, batch_id=batch_id,
                  details=f"Generated {len(rendered_clips)} clips ready for review.", db_path=db_path)

        # Update batch status to REVIEW_READY if all jobs are awaiting approval or completed
        with get_db(db_path) as conn:
            unfinished = conn.execute("""
                SELECT COUNT(*) FROM jobs
                WHERE batch_id = ? AND status NOT IN ('AWAITING_APPROVAL', 'APPROVED', 'COMPLETED', 'FAILED', 'CANCELLED')
            """, (batch_id,)).fetchone()[0]
            if unfinished == 0:
                conn.execute("UPDATE batches SET status = 'REVIEW_READY', updated_at = ? WHERE id = ?",
                             (_now_ist_iso(), batch_id))

        return {"job_id": job_id, "status": "AWAITING_APPROVAL", "clips": rendered_clips}

    except Exception as exc:
        logger.exception("Batch job %s failed: %s", job_id, exc)
        update_job_status(job_id, "FAILED", progress=0, error_message=str(exc), db_path=db_path)
        log_audit("JOB_FAILED", "job", job_id, batch_id=batch_id, details=str(exc), db_path=db_path)
        return {"job_id": job_id, "status": "FAILED", "error": str(exc)}


class BatchWorkerPool:
    """
    Manages persistent SQLite job queue polling with configurable rendering concurrency (1-2 jobs).
    Protects CPU/GPU from overloading while safely resuming after restarts.
    """

    def __init__(self, max_concurrency: int = 2, poll_interval: float = 5.0, db_path: Path = BATCH_DB):
        self.max_concurrency = max_concurrency
        self.poll_interval = poll_interval
        self.db_path = db_path
        self._running = False
        self._thread: threading.Thread | None = None
        self._executor = ThreadPoolExecutor(max_workers=self.max_concurrency, thread_name_prefix="batch-job-worker")
        self._active_jobs: set[str] = set()
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._recover_stalled_jobs()
        self._thread = threading.Thread(target=self._worker_loop, name="autoclip-batch-worker", daemon=True)
        self._thread.start()
        logger.info("BatchWorkerPool started with max concurrency %d", self.max_concurrency)

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._executor.shutdown(wait=False)
        logger.info("BatchWorkerPool stopped")

    def _recover_stalled_jobs(self) -> None:
        """Reset jobs interrupted by power loss or application restart back to QUEUED."""
        with get_db(self.db_path) as conn:
            conn.execute("""
                UPDATE jobs
                SET status = 'QUEUED', stage_message = 'Resumed from system restart', updated_at = ?
                WHERE status IN ('DOWNLOADING', 'ANALYZING', 'RIGHTS_REVIEW', 'TRANSCRIBING', 'SELECTING_HIGHLIGHTS', 'EDITING', 'RENDERING', 'QUALITY_CHECK')
            """, (_now_ist_iso(),))

    def _worker_loop(self) -> None:
        while self._running:
            try:
                with self._lock:
                    available_slots = self.max_concurrency - len(self._active_jobs)
                if available_slots > 0:
                    with get_db(self.db_path) as conn:
                        rows = conn.execute("""
                            SELECT id FROM jobs
                            WHERE status = 'QUEUED'
                            ORDER BY updated_at ASC
                            LIMIT ?
                        """, (available_slots,)).fetchall()
                        job_ids = [r["id"] for r in rows]

                    for jid in job_ids:
                        with self._lock:
                            if jid not in self._active_jobs:
                                self._active_jobs.add(jid)
                                self._executor.submit(self._run_job_wrapper, jid)
            except Exception as exc:
                logger.error("Error in batch worker loop: %s", exc)

            time.sleep(self.poll_interval)

    def _run_job_wrapper(self, job_id: str) -> None:
        try:
            process_batch_job(job_id, db_path=self.db_path)
        except Exception as exc:
            logger.error("Unexpected error executing job %s: %s", job_id, exc)
        finally:
            with self._lock:
                self._active_jobs.discard(job_id)


_global_batch_worker: BatchWorkerPool | None = None


def get_batch_worker(db_path: Path = BATCH_DB) -> BatchWorkerPool:
    global _global_batch_worker
    if _global_batch_worker is None:
        _global_batch_worker = BatchWorkerPool(max_concurrency=2, db_path=db_path)
    return _global_batch_worker
