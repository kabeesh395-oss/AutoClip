from __future__ import annotations

import io
import json
import logging
import re
import shutil
import socket
import sqlite3
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yt_dlp
from fastapi import BackgroundTasks, Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .batch_manager import (
    _now_ist_iso,
    add_source_to_batch,
    create_batch,
    create_creator,
    generate_batch_schedule_slots,
    get_batch,
    get_batch_report,
    get_creator,
    get_db,
    init_batch_tables,
    list_batches,
    list_creators,
    list_game_policies,
    register_game_policy,
    set_clip_approval,
)
from .batch_worker import get_batch_worker
from .pipeline import Candidate, generate_viral_intelligence, process
from .youtube import connect_youtube, schedule_youtube_video, upload_public_video, youtube_status

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
OUTPUTS = DATA / "outputs"
STATIC = Path(__file__).parent / "static"
SCHEDULE_DB = DATA / "autoclip.sqlite3"
LOG_FILE = DATA / "server.log"
IST = timezone(timedelta(hours=5, minutes=30))
for folder in (UPLOADS, OUTPUTS):
    folder.mkdir(parents=True, exist_ok=True)
init_batch_tables()

# Configure persistent file logger
logger = logging.getLogger("autoclip")
logger.setLevel(logging.INFO)
if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
    fh = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
    fh.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)
    logging.getLogger("uvicorn").addHandler(fh)
    logging.getLogger("uvicorn.access").addHandler(fh)

app = FastAPI(title="AutoClip AI", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    start_time = time.time()
    logger.info("--> %s %s from %s", request.method, request.url.path, client_ip)
    try:
        response = await call_next(request)
        duration_ms = (time.time() - start_time) * 1000
        logger.info("<-- %s %s [%s] in %.1fms", request.method, request.url.path, response.status_code, duration_ms)
        return response
    except Exception as exc:
        duration_ms = (time.time() - start_time) * 1000
        logger.error("<-- ERROR %s %s: %s (%.1fms)", request.method, request.url.path, exc, duration_ms, exc_info=True)
        raise


app.mount("/outputs", StaticFiles(directory=OUTPUTS), name="outputs")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
jobs: dict[str, dict[str, Any]] = {}
lock = threading.Lock()
_scheduler_started = False


@app.get("/api/server-logs")
def api_get_server_logs():
    """Returns recent server logs with secrets and sensitive tokens masked."""
    if not LOG_FILE.exists():
        return {"logs": [], "count": 0}
    lines = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]
    masked_lines = []
    for line in lines:
        clean = re.sub(
            r'(client_secret|access_token|refresh_token|password)[\'"]?\s*[:=]\s*[\'"][^\'"]+[\'"]',
            r'\1="[REDACTED]"',
            line,
            flags=re.I,
        )
        masked_lines.append(clean)
    return {"logs": masked_lines, "count": len(masked_lines)}


@contextmanager
def _db():
    DATA.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(SCHEDULE_DB, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            run_at TEXT NOT NULL,
            language TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT 'small',
            clip_count INTEGER NOT NULL DEFAULT 5,
            status TEXT NOT NULL DEFAULT 'queued',
            job_id TEXT,
            error TEXT,
            created_at TEXT NOT NULL
        )
    """)
        connection.commit()
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _now_ist() -> datetime:
    return datetime.now(IST)


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "AutoClip AI", "mode": "local-first", "ip": _get_local_ip()}


@app.get("/api/server-info")
def get_server_info():
    local_ip = _get_local_ip()
    apk_file = ROOT / "android-app" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    apk_exists = apk_file.exists() and apk_file.stat().st_size > 0
    apk_size_mb = round(apk_file.stat().st_size / (1024 * 1024), 1) if apk_exists else 0
    yt_stat = youtube_status()

    return {
        "app_name": "AutoClip AI",
        "version": "1.0.0",
        "local_ip": local_ip,
        "port": 8765,
        "web_url": f"http://{local_ip}:8765",
        "apk_available": apk_exists,
        "apk_size_mb": apk_size_mb,
        "apk_download_url": f"/api/download-apk",
        "youtube_connected": yt_stat.get("connected", False),
        "timezone": "Asia/Kolkata",
        "default_batch_config": {
            "videos_per_day": 3,
            "schedule_days": 3,
            "batch_target": 9,
            "daily_shorts": 2,
            "daily_long": 1,
            "manual_approval_required": True,
            "slots": ["12:00 PM (Short)", "04:00 PM (Short)", "07:00 PM (Long)"]
        }
    }


@app.get("/api/download-apk")
def download_apk():
    apk_path = ROOT / "android-app" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    if not apk_path.exists() or apk_path.stat().st_size == 0:
        raise HTTPException(404, "Android APK not found. Build the project first.")
    return FileResponse(
        str(apk_path),
        media_type="application/vnd.android.package-archive",
        filename="AutoClip-AI-debug.apk"
    )


@app.get("/api/youtube/status")
def get_youtube_status():
    return youtube_status()


@app.post("/api/youtube/connect")
def youtube_connect():
    try:
        return connect_youtube()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


def _run_job(job_id: str, source: Path, language: str | None, model: str,
             count: int, publish_public: bool = False):
    try:
        with lock:
            jobs[job_id]["status"] = "transcribing"
            jobs[job_id]["message"] = "Transcribing locally with Faster-Whisper"
        clips = process(source, OUTPUTS / job_id, language, model, count)
        with lock:
            jobs[job_id]["clips"] = clips
        if publish_public:
            if not youtube_status()["connected"]:
                raise RuntimeError("YouTube is not connected. Connect your channel before processing public uploads.")
            for index, clip in enumerate(clips, 1):
                with lock:
                    jobs[job_id]["status"] = "uploading"
                    jobs[job_id]["message"] = f"Uploading public Short {index}/{len(clips)} to YouTube"
                video_path = OUTPUTS / job_id / Path(clip["video_url"]).name
                uploaded = upload_public_video(
                    video_path,
                    clip.get("youtube_title", clip["title"]),
                    clip.get("youtube_description", clip["reason"]),
                    clip.get("youtube_tags", []),
                )
                clip.update(youtube_id=uploaded["video_id"], youtube_url=uploaded["url"],
                            upload_status="public")
                with lock:
                    jobs[job_id]["clips"] = clips
        with lock:
            jobs[job_id].update(status="completed",
                message=("All clips processed and published publicly." if publish_public else "Clips ready"),
                clips=clips)
    except Exception as exc:
        with lock:
            jobs[job_id].update(status="failed", message=str(exc))


def _download_url(job_id: str, url: str, mode: str) -> Path:
    target = UPLOADS / job_id
    target.mkdir(parents=True, exist_ok=True)
    options = {"outtmpl": str(target / "%(title).80s-%(id)s.%(ext)s"),
               "format": "bv*+ba/b", "merge_output_format": "mp4",
               "noplaylist": True, "quiet": True, "no_warnings": True, "color": "no_color"}
    if mode == "live":
        options["live_from_start"] = True
        options["wait_for_video"] = 10
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
        filename = Path(downloader.prepare_filename(info))
    if not filename.exists():
        merged = filename.with_suffix(".mp4")
        if merged.exists():
            filename = merged
    if not filename.exists():
        matches = [p for p in target.glob("*")
                   if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
        if not matches:
            raise RuntimeError("Downloader finished but no video file was found.")
        filename = matches[0]
    return filename


@app.post("/api/jobs/upload")
async def upload_video(background: BackgroundTasks, file: UploadFile = File(...),
                       language: str = Form(""), model: str = Form("small"),
                       count: int = Form(5), publish_public: bool = Form(False)):
    if publish_public and not youtube_status()["connected"]:
        raise HTTPException(400, "Connect YouTube first before enabling public upload.")
    suffix = Path(file.filename or "video.mp4").suffix.lower()
    if suffix not in {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}:
        raise HTTPException(400, "Unsupported video format.")
    job_id = uuid.uuid4().hex[:12]
    destination = UPLOADS / f"{job_id}{suffix}"
    with destination.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    jobs[job_id] = {"id": job_id, "status": "queued", "message": "Video uploaded",
                    "source": file.filename, "clips": []}
    background.add_task(_run_job, job_id, destination, language or None, model,
                        max(1, min(count, 10)), publish_public)
    return jobs[job_id]


@app.post("/api/jobs/url")
def create_url_job(background: BackgroundTasks, url: str = Form(...),
                   mode: str = Form("vod"), language: str = Form(""),
                   model: str = Form("small"), count: int = Form(5),
                   publish_public: bool = Form(False)):
    if not url.startswith(("https://", "http://")):
        raise HTTPException(400, "Enter a valid http(s) video URL.")
    if mode not in {"vod", "live"}:
        raise HTTPException(400, "Mode must be vod or live.")
    if publish_public and not youtube_status()["connected"]:
        raise HTTPException(400, "Connect YouTube first. Public upload is enabled for this job.")
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"id": job_id, "status": "downloading",
                    "message": "Downloading source", "source": url, "clips": []}

    def download_then_process():
        try:
            with lock:
                jobs[job_id]["message"] = "Downloading / recording source"
            source = _download_url(job_id, url, mode)
            with lock:
                jobs[job_id]["message"] = "Source ready; starting AI analysis"
            _run_job(job_id, source, language or None, model,
                     max(1, min(count, 10)), publish_public)
        except Exception as exc:
            with lock:
                jobs[job_id].update(status="failed", message=str(exc))

    background.add_task(download_then_process)
    return jobs[job_id]


@app.post("/api/schedule")
def add_scheduled_source(url: str = Form(...), run_at: str = Form(...),
                         language: str = Form(""), model: str = Form("small"),
                         count: int = Form(5)):
    if not youtube_status()["connected"]:
        raise HTTPException(400, "Connect YouTube before scheduling automatic public uploads.")
    if not url.startswith(("https://", "http://")):
        raise HTTPException(400, "Enter a valid http(s) video URL.")
    try:
        scheduled = datetime.fromisoformat(run_at)
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=IST)
        scheduled = scheduled.astimezone(IST)
    except ValueError as exc:
        raise HTTPException(400, "Invalid schedule date/time.") from exc
    if scheduled <= _now_ist():
        raise HTTPException(400, "Choose a future date and time.")
    with _db() as conn:
        day = scheduled.date().isoformat()
        existing = conn.execute(
            "SELECT id FROM scheduled_sources WHERE substr(run_at,1,10)=? "
            "AND status IN ('queued','running','completed') LIMIT 1", (day,)
        ).fetchone()
        if existing:
            raise HTTPException(409, f"A source is already queued or processed for {day}.")
        cursor = conn.execute(
            "INSERT INTO scheduled_sources(url,run_at,language,model,clip_count,status,created_at) "
            "VALUES(?,?,?,?,?,'queued',?)",
            (url, scheduled.isoformat(), language, model, max(1, min(count, 5)), _now_ist().isoformat())
        )
        item_id = cursor.lastrowid
    return {"id": item_id, "status": "queued", "run_at": scheduled.isoformat(),
            "message": "One source queued. AutoClip will create and publish up to five public Shorts."}


@app.get("/api/schedule")
def list_scheduled_sources():
    with _db() as conn:
        rows = conn.execute(
            "SELECT id,url,run_at,status,job_id,error FROM scheduled_sources ORDER BY run_at DESC LIMIT 30"
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


def _scheduled_worker():
    while True:
        try:
            with _db() as conn:
                row = conn.execute(
                    "SELECT * FROM scheduled_sources WHERE status='queued' AND run_at<=? "
                    "ORDER BY run_at LIMIT 1", (_now_ist().isoformat(),)
                ).fetchone()
                if row:
                    conn.execute("UPDATE scheduled_sources SET status='running' WHERE id=? AND status='queued'",
                                 (row["id"],))
                    conn.commit()
            if row:
                job_id = uuid.uuid4().hex[:12]
                jobs[job_id] = {"id": job_id, "status": "downloading",
                    "message": "Scheduled source download started", "source": row["url"], "clips": []}
                with _db() as conn:
                    conn.execute("UPDATE scheduled_sources SET job_id=? WHERE id=?", (job_id, row["id"]))
                    conn.commit()
                try:
                    source = _download_url(job_id, row["url"], "vod")
                    _run_job(job_id, source, row["language"] or None, row["model"],
                             row["clip_count"], True)
                    state = jobs[job_id]
                    final_status = "completed" if state["status"] == "completed" else "failed"
                    with _db() as conn:
                        conn.execute("UPDATE scheduled_sources SET status=?,error=? WHERE id=?",
                            (final_status, "" if final_status == "completed" else state["message"], row["id"]))
                        conn.commit()
                except Exception as exc:
                    with _db() as conn:
                        conn.execute("UPDATE scheduled_sources SET status='failed',error=? WHERE id=?",
                                     (str(exc), row["id"]))
                        conn.commit()
        except Exception:
            pass
        time.sleep(15)


@app.on_event("startup")
def start_scheduler():
    global _scheduler_started
    with _db():
        pass
    init_batch_tables()
    get_batch_worker().start()
    if not _scheduler_started:
        threading.Thread(target=_scheduled_worker, name="autoclip-scheduler", daemon=True).start()
        _scheduler_started = True


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found. Jobs are stored in memory until the app restarts.")
        return dict(job)


@app.get("/api/history")
def get_history():
    with lock:
        return {"jobs": list(jobs.values())[::-1]}


@app.post("/api/clips/{job_id}/{clip_id}/regenerate-metadata")
def regenerate_clip_metadata(job_id: str, clip_id: int):
    with lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found.")
        target_clip = None
        for c in job.get("clips", []):
            if c.get("id") == clip_id:
                target_clip = c
                break
        if not target_clip:
            raise HTTPException(404, "Clip not found.")

        # Re-run viral intelligence generator
        cand = Candidate(
            start=float(target_clip.get("start", 0)),
            end=float(target_clip.get("end", 0)),
            title=str(target_clip.get("title", "")),
            reason=str(target_clip.get("reason", "")),
            score=float(target_clip.get("score", 0)),
            transcript=str(target_clip.get("description", target_clip.get("title", "")))
        )
        intel = generate_viral_intelligence(cand)
        target_clip["hook"] = intel.get("hook", target_clip.get("hook"))
        target_clip["titles"] = intel.get("titles", target_clip.get("titles", []))
        target_clip["youtube_title"] = target_clip["titles"][0] if target_clip["titles"] else target_clip.get("youtube_title")
        target_clip["description"] = intel.get("description", target_clip.get("description"))
        target_clip["youtube_description"] = target_clip["description"]
        target_clip["hashtags"] = intel.get("hashtags", target_clip.get("hashtags", []))
        target_clip["youtube_tags"] = [t.lstrip("#") for t in target_clip["hashtags"]]
        target_clip["scene_intelligence"] = intel.get("scene_intelligence", target_clip.get("scene_intelligence"))

        # Persist updated clips.json
        job_dir = OUTPUTS / job_id
        if job_dir.exists():
            (job_dir / "clips.json").write_text(json.dumps(job["clips"], ensure_ascii=False, indent=2), encoding="utf-8")

        return target_clip


@app.put("/api/clips/{job_id}/{clip_id}")
def update_clip(job_id: str, clip_id: int, payload: dict = Body(...)):
    with lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found.")
        target_clip = None
        for c in job.get("clips", []):
            if c.get("id") == clip_id:
                target_clip = c
                break
        if not target_clip:
            raise HTTPException(404, "Clip not found.")

        if "title" in payload:
            target_clip["title"] = payload["title"]
        if "youtube_title" in payload:
            target_clip["youtube_title"] = payload["youtube_title"]
        if "description" in payload:
            target_clip["description"] = payload["description"]
            target_clip["youtube_description"] = payload["description"]
        if "hashtags" in payload and isinstance(payload["hashtags"], list):
            target_clip["hashtags"] = payload["hashtags"]
            target_clip["youtube_tags"] = [t.lstrip("#") for t in payload["hashtags"]]
        if "hook" in payload and isinstance(payload["hook"], dict):
            target_clip["hook"] = payload["hook"]
        if "subtitles_srt" in payload:
            target_clip["subtitles_srt"] = payload["subtitles_srt"]
            job_dir = OUTPUTS / job_id
            if job_dir.exists():
                srt_path = job_dir / Path(target_clip.get("subtitle_url", "")).name
                if srt_path.name:
                    srt_path.write_text(payload["subtitles_srt"], encoding="utf-8")

        job_dir = OUTPUTS / job_id
        if job_dir.exists():
            (job_dir / "clips.json").write_text(json.dumps(job["clips"], ensure_ascii=False, indent=2), encoding="utf-8")

        return target_clip


# ==========================================
# BATCH PRODUCTION API ENDPOINTS (FR-01 to FR-15)
# ==========================================

@app.post("/api/batches")
def api_create_batch(payload: dict = Body(...)):
    name = str(payload.get("name", "New Batch")).strip()
    target_per_day = int(payload.get("target_videos_per_day", 3))
    days = int(payload.get("schedule_days", 3))
    shorts = int(payload.get("daily_shorts", 2))
    longs = int(payload.get("daily_long", 1))
    lang = str(payload.get("language", "en"))
    model = str(payload.get("whisper_model", "small"))
    return create_batch(
        name=name,
        target_videos_per_day=target_per_day,
        schedule_days=days,
        daily_shorts=shorts,
        daily_long=longs,
        language=lang,
        whisper_model=model,
    )


@app.get("/api/batches")
def api_list_batches():
    return {"batches": list_batches()}


@app.get("/api/batches/{batch_id}")
def api_get_batch(batch_id: str):
    batch = get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    return batch


@app.post("/api/batches/{batch_id}/sources")
def api_add_source(batch_id: str, payload: dict = Body(...)):
    url = str(payload.get("source_url", "")).strip()
    if not url:
        raise HTTPException(400, "source_url is required")
    creator_id = payload.get("creator_id")
    game_name = payload.get("game_name")
    title = payload.get("title", "")
    try:
        return add_source_to_batch(batch_id, url, creator_id=creator_id, game_name=game_name, title=title)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/batches/{batch_id}/upload-source")
async def api_upload_batch_source(
    batch_id: str,
    file: UploadFile = File(...),
    creator_id: str = Form(""),
    game_name: str = Form("")
):
    batch = get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    dest_dir = UPLOADS / f"batch_{batch_id}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "source.mp4").suffix.lower()
    if suffix not in {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}:
        raise HTTPException(400, "Unsupported video format.")
    dest_file = dest_dir / f"{uuid.uuid4().hex[:8]}_{file.filename}"
    with dest_file.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return add_source_to_batch(
        batch_id=batch_id,
        source_url=str(dest_file),
        creator_id=creator_id or None,
        game_name=game_name or None,
        title=Path(file.filename or "video").stem,
        file_path=str(dest_file)
    )


@app.post("/api/creators")
def api_create_creator(payload: dict = Body(...)):
    name = str(payload.get("name", "")).strip()
    channel = str(payload.get("channel_url", "")).strip()
    if not name or not channel:
        raise HTTPException(400, "name and channel_url are required")
    status = str(payload.get("permission_status", "ACTIVE"))
    comm = str(payload.get("commercial_use_status", "ALLOWED"))
    return create_creator(name, channel, permission_status=status, commercial_use_status=comm)


@app.get("/api/creators")
def api_list_creators():
    return {"creators": list_creators()}


@app.post("/api/game-policies")
def api_register_game_policy(payload: dict = Body(...)):
    game_name = str(payload.get("game_name", "")).strip()
    if not game_name:
        raise HTTPException(400, "game_name is required")
    publisher = str(payload.get("publisher_name", "")).strip()
    policy = str(payload.get("commercial_policy", "VERIFIED")).strip()
    monetization = int(payload.get("monetization_allowed", 1))
    notes = str(payload.get("notes", "")).strip()
    return register_game_policy(
        game_name=game_name,
        publisher_name=publisher,
        commercial_policy=policy,
        monetization_allowed=monetization,
        notes=notes
    )


@app.get("/api/game-policies")
def api_list_game_policies():
    return {"game_policies": list_game_policies()}


@app.put("/api/clips/{clip_id}/approval")
def api_set_clip_approval(clip_id: str, payload: dict = Body(...)):
    status = str(payload.get("approval_status", "APPROVED")).upper()
    try:
        return set_clip_approval(clip_id, status)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/batches/{batch_id}/schedule")
def api_schedule_batch(batch_id: str, payload: dict = Body(default={})):
    start_date = payload.get("start_date")
    try:
        slots = generate_batch_schedule_slots(batch_id, start_date_str=start_date)
        return {"scheduled_slots": slots, "count": len(slots)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/batches/{batch_id}/report")
def api_batch_report(batch_id: str):
    try:
        return get_batch_report(batch_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.get("/api/batches/{batch_id}/download-zip")
def api_download_batch_zip(batch_id: str):
    batch = get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")

    with get_db() as conn:
        approved_clips = conn.execute("""
            SELECT * FROM clips WHERE batch_id = ? AND approval_status = 'APPROVED'
            ORDER BY created_at ASC
        """, (batch_id,)).fetchall()

    if not approved_clips:
        raise HTTPException(400, "No approved clips found in this batch to export. Approve clips first.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        metadata_export = []
        for idx, c in enumerate(approved_clips, 1):
            clip_dict = dict(c)
            p = Path(clip_dict["output_path"]) if clip_dict.get("output_path") else None
            format_prefix = "SHORT" if clip_dict["format"] == "SHORT" else "LONG"
            safe_slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", clip_dict["title"])[:35] or f"clip_{idx}"

            arc_video_name = f"{idx:02d}_{format_prefix}_{safe_slug}.mp4"
            if p and p.exists():
                zip_file.write(str(p), arcname=arc_video_name)

                srt_p = p.with_suffix(".srt")
                if srt_p.exists():
                    zip_file.write(str(srt_p), arcname=f"{idx:02d}_{format_prefix}_{safe_slug}.srt")

            try:
                titles = json.loads(clip_dict.get("titles_json") or "[]")
            except Exception:
                titles = [clip_dict["title"]]
            try:
                tags = json.loads(clip_dict.get("hashtags_json") or "[]")
            except Exception:
                tags = []
            try:
                hook = json.loads(clip_dict.get("hook_json") or "{}")
            except Exception:
                hook = {}

            metadata_export.append({
                "index": idx,
                "format": clip_dict["format"],
                "video_file": arc_video_name,
                "title": clip_dict["title"],
                "titles": titles,
                "description": clip_dict["description"],
                "hashtags": tags,
                "hook": hook,
                "duration": clip_dict["duration"]
            })

        zip_file.writestr("batch_metadata.json", json.dumps(metadata_export, ensure_ascii=False, indent=2))

    buffer.seek(0)
    zip_filename = f"batch_{batch_id}_approved_clips.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'}
    )


@app.get("/api/batches/{batch_id}/clips")
def api_get_batch_clips(batch_id: str):
    batch = get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    formatted_clips = []
    for c in batch.get("clips", []):
        clip_copy = dict(c)
        try:
            clip_copy["titles"] = json.loads(c.get("titles_json") or "[]")
        except Exception:
            clip_copy["titles"] = [c.get("title", "")]
        try:
            clip_copy["hashtags"] = json.loads(c.get("hashtags_json") or "[]")
        except Exception:
            clip_copy["hashtags"] = []
        try:
            clip_copy["hook"] = json.loads(c.get("hook_json") or "{}")
        except Exception:
            clip_copy["hook"] = {}
        try:
            clip_copy["scene_intelligence"] = json.loads(c.get("scene_intelligence_json") or "{}")
        except Exception:
            clip_copy["scene_intelligence"] = {}
        try:
            clip_copy["improvement_tips"] = json.loads(c.get("improvement_tips_json") or "[]")
        except Exception:
            clip_copy["improvement_tips"] = []
        if c.get("output_path"):
            p = Path(c["output_path"])
            clip_copy["video_url"] = f"/outputs/{p.parent.parent.name}/{p.parent.name}/{p.name}"
            srt_p = p.with_suffix(".srt")
            if srt_p.exists():
                clip_copy["subtitle_url"] = f"/outputs/{p.parent.parent.name}/{p.parent.name}/{srt_p.name}"
                try:
                    clip_copy["subtitles_srt"] = srt_p.read_text(encoding="utf-8")
                except Exception:
                    clip_copy["subtitles_srt"] = ""
            ass_p = p.with_suffix(".ass")
            if ass_p.exists():
                clip_copy["ass_url"] = f"/outputs/{p.parent.parent.name}/{p.parent.name}/{ass_p.name}"
        formatted_clips.append(clip_copy)
    return {"clips": formatted_clips, "count": len(formatted_clips)}


@app.get("/api/clips/{clip_id}")
def api_get_clip(clip_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Clip not found")
        clip = dict(row)
        try:
            clip["titles"] = json.loads(clip.get("titles_json") or "[]")
        except Exception:
            clip["titles"] = [clip.get("title", "")]
        try:
            clip["hashtags"] = json.loads(clip.get("hashtags_json") or "[]")
        except Exception:
            clip["hashtags"] = []
        try:
            clip["hook"] = json.loads(clip.get("hook_json") or "{}")
        except Exception:
            clip["hook"] = {}
        try:
            clip["scene_intelligence"] = json.loads(clip.get("scene_intelligence_json") or "{}")
        except Exception:
            clip["scene_intelligence"] = {}
        try:
            clip["improvement_tips"] = json.loads(clip.get("improvement_tips_json") or "[]")
        except Exception:
            clip["improvement_tips"] = []
        if clip.get("output_path"):
            p = Path(clip["output_path"])
            clip["video_url"] = f"/outputs/{p.parent.parent.name}/{p.parent.name}/{p.name}"
            srt_p = p.with_suffix(".srt")
            if srt_p.exists():
                clip["subtitle_url"] = f"/outputs/{p.parent.parent.name}/{p.parent.name}/{srt_p.name}"
                try:
                    clip["subtitles_srt"] = srt_p.read_text(encoding="utf-8")
                except Exception:
                    clip["subtitles_srt"] = ""
            ass_p = p.with_suffix(".ass")
            if ass_p.exists():
                clip["ass_url"] = f"/outputs/{p.parent.parent.name}/{p.parent.name}/{ass_p.name}"
        return clip


@app.post("/api/clips/{clip_id}/re-render")
def api_rerender_clip(clip_id: str, payload: dict = Body(default={})):
    """Re-render clip with custom Opus Clip layout and kinetic subtitle styling."""
    layout = str(payload.get("layout", "FIT_BLUR")).upper()
    caption_style = str(payload.get("caption_style", "KINETIC_YELLOW")).upper()
    show_hook_header = bool(payload.get("show_hook_header", True))

    with get_db() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Clip not found")
        clip = dict(row)

        source_row = conn.execute("SELECT * FROM sources WHERE id = ?", (clip["source_id"],)).fetchone()
        if not source_row:
            raise HTTPException(404, "Source video not found")
        source = dict(source_row)

    source_path = Path(source.get("file_path") or source.get("source_url") or "")
    if not source_path.exists():
        cand_path = UPLOADS / f"batch_{clip['batch_id']}" / f"source_{clip['source_id']}.mp4"
        if cand_path.exists():
            source_path = cand_path
        else:
            raise HTTPException(400, "Source media file is not available locally for re-rendering.")

    output_path = Path(clip["output_path"]) if clip.get("output_path") else (OUTPUTS / f"batch_{clip['batch_id']}" / f"{clip_id}.mp4")

    from .pipeline import Candidate, Segment, render
    c_cand = Candidate(
        start=float(clip["start_time"]),
        end=float(clip["end_time"]),
        title=clip["title"],
        reason="Opus Re-render",
        score=float(clip.get("hook_score") or 90.0),
        transcript=clip.get("transcript") or ""
    )
    rows = [Segment(c_cand.start, c_cand.end, c_cand.transcript)]

    try:
        render(
            source=source_path,
            rows=rows,
            clip=c_cand,
            target=output_path,
            layout=layout,
            caption_style=caption_style,
            show_hook_header=show_hook_header
        )
    except Exception as exc:
        raise HTTPException(500, f"Re-render failed: {str(exc)}")

    with get_db() as conn:
        conn.execute("""
            UPDATE clips SET layout = ?, caption_style = ?, output_path = ? WHERE id = ?
        """, (layout, caption_style, str(output_path), clip_id))

    return {
        "status": "re-rendered",
        "clip_id": clip_id,
        "layout": layout,
        "caption_style": caption_style,
        "video_url": f"/outputs/{output_path.parent.parent.name}/{output_path.parent.name}/{output_path.name}"
    }


@app.post("/api/batches/{batch_id}/start")
def api_start_batch(batch_id: str):
    batch = get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    worker = get_batch_worker()
    worker.start()
    return {"status": "started", "batch_id": batch_id, "jobs_count": len(batch.get("jobs", []))}


@app.post("/api/jobs/{job_id}/retry")
def api_retry_job(job_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Job not found")
        conn.execute("UPDATE jobs SET status = 'QUEUED', retry_count = retry_count + 1, error_message = '', stage_message = 'Retried by user', updated_at = ? WHERE id = ?",
                     (_now_ist_iso(), job_id))
    return {"status": "QUEUED", "job_id": job_id}


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(job_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Job not found")
        conn.execute("UPDATE jobs SET status = 'CANCELLED', stage_message = 'Cancelled by user', updated_at = ? WHERE id = ?",
                     (_now_ist_iso(), job_id))
    return {"status": "CANCELLED", "job_id": job_id}


@app.post("/api/batches/{batch_id}/commit-cloud-schedule")
def api_commit_cloud_schedule(batch_id: str):
    yt_stat = youtube_status()
    if not yt_stat.get("connected"):
        raise HTTPException(400, "YouTube account is not connected. Connect YouTube in settings before cloud scheduling.")

    batch = get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")

    with get_db() as conn:
        scheduled_slots = conn.execute("""
            SELECT s.*, c.output_path, c.title, c.description, c.hashtags_json, c.approval_status
            FROM schedules s
            JOIN clips c ON s.clip_id = c.id
            WHERE s.batch_id = ? AND s.status = 'PENDING'
            ORDER BY s.scheduled_at ASC
        """, (batch_id,)).fetchall()

    if not scheduled_slots:
        raise HTTPException(400, "No pending scheduled slots found for this batch. Generate schedule slots first.")

    committed_results = []
    for slot in scheduled_slots:
        if slot["approval_status"] != "APPROVED":
            continue
        video_p = Path(slot["output_path"])
        if not video_p.exists():
            continue
        try:
            tags = json.loads(slot.get("hashtags_json") or "[]")
        except Exception:
            tags = []
        tags = [str(t).lstrip("#") for t in tags]

        try:
            res = schedule_youtube_video(
                video_path=video_p,
                title=slot["title"],
                description=slot["description"],
                publish_at_iso=slot["scheduled_at"],
                tags=tags
            )
            with get_db() as conn:
                conn.execute("""
                    UPDATE schedules
                    SET status = 'COMMITTED', youtube_video_id = ?
                    WHERE id = ?
                """, (res["video_id"], slot["id"]))
            committed_results.append({
                "slot_id": slot["id"],
                "video_id": res["video_id"],
                "url": res["url"],
                "scheduled_at": slot["scheduled_at"]
            })
        except Exception as exc:
            with get_db() as conn:
                conn.execute("UPDATE schedules SET error_message = ? WHERE id = ?", (str(exc), slot["id"]))

    return {"status": "committed", "count": len(committed_results), "slots": committed_results}


@app.post("/api/worker/poll")
def api_worker_poll(payload: dict = Body(default={})):
    """Worker polling endpoint for local cloud rendering worker."""
    worker_id = str(payload.get("worker_id", "pc-local-worker"))
    batch_id = payload.get("batch_id")
    with get_db() as conn:
        if batch_id:
            job = conn.execute("""
                SELECT j.*, s.source_url, s.title as source_title, b.language, b.whisper_model, b.daily_shorts, b.daily_long
                FROM jobs j
                JOIN sources s ON j.source_id = s.id
                JOIN batches b ON j.batch_id = b.id
                WHERE j.status = 'QUEUED' AND j.batch_id = ?
                ORDER BY j.updated_at ASC
                LIMIT 1
            """, (batch_id,)).fetchone()
        else:
            job = conn.execute("""
                SELECT j.*, s.source_url, s.title as source_title, b.language, b.whisper_model, b.daily_shorts, b.daily_long
                FROM jobs j
                JOIN sources s ON j.source_id = s.id
                JOIN batches b ON j.batch_id = b.id
                WHERE j.status = 'QUEUED'
                ORDER BY j.updated_at ASC
                LIMIT 1
            """).fetchone()
        if not job:
            return {"has_job": False}
        job_dict = dict(job)
        conn.execute("UPDATE jobs SET status = 'PROCESSING', stage_message = 'Rendering on Worker', updated_at = ? WHERE id = ?",
                     (_now_ist_iso(), job_dict["id"]))
        return {"has_job": True, "job": job_dict}


@app.post("/api/worker/jobs/{job_id}/complete")
def api_worker_complete_job(job_id: str, payload: dict = Body(default={})):
    """Worker reports rendered clips completed."""
    clips = payload.get("clips", [])
    now = _now_ist_iso()
    with get_db() as conn:
        for c in clips:
            clip_id = f"clip_{uuid.uuid4().hex[:8]}"
            conn.execute("""
                INSERT INTO clips (
                    id, batch_id, source_id, format, start_time, end_time, duration,
                    hook_score, virality_grade, flow_score, engagement_score, trend_score,
                    layout, caption_style, why_viral, improvement_tips_json,
                    title, titles_json, description, hashtags_json, hook_json,
                    output_path, approval_status, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, 'PENDING', ?
                )
            """, (
                clip_id, c.get("batch_id"), c.get("source_id"), c.get("format", "SHORT"),
                float(c.get("start_time", 0.0)), float(c.get("end_time", 15.0)), float(c.get("duration", 15.0)),
                float(c.get("hook_score", 85.0)), c.get("virality_grade", "A"), float(c.get("flow_score", 80.0)),
                float(c.get("engagement_score", 85.0)), float(c.get("trend_score", 80.0)),
                c.get("layout", "FIT_BLUR"), c.get("caption_style", "KINETIC_YELLOW"), c.get("why_viral", ""),
                json.dumps(c.get("improvement_tips", [])),
                c.get("title", "Clip"), json.dumps(c.get("titles", [c.get("title", "Clip")])),
                c.get("description", ""), json.dumps(c.get("hashtags", [])), json.dumps(c.get("hook", {})),
                c.get("local_video_path", ""), now
            ))
        conn.execute("UPDATE jobs SET status = 'COMPLETED', progress = 100, stage_message = 'Completed', completed_at = ?, updated_at = ? WHERE id = ?",
                     (now, now, job_id))
    return {"status": "COMPLETED", "job_id": job_id, "clips_created": len(clips)}
