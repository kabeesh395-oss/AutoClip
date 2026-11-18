import json
import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add AutoClip to path
ROOT = Path("E:/autoclip/AutoClip").resolve()
sys.path.insert(0, str(ROOT))

from faster_whisper import WhisperModel
from autoclip_app.pipeline import Candidate, Segment, render
from autoclip_app.youtube import upload_public_video, youtube_status
from autoclip_app.production.models import ContentItem, PlatformType, PrivacyStatus, PublishSchedule, PublishStatus
from autoclip_app.production.storage import ProductionStorage
from autoclip_app.production.content_producer import compute_file_sha256

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("unique_long_shorts")

print("=" * 65, flush=True)
print("🚀 PRODUCING & UPLOADING UNIQUE 45s - 90s GTA 6 VIRAL SHORTS", flush=True)
print("=" * 65, flush=True)

# 1. Verify YouTube status
yt_stat = youtube_status()
print(f"YouTube status: {yt_stat}", flush=True)
if not yt_stat.get("connected"):
    raise RuntimeError("YouTube channel is not connected!")

output_dir = ROOT / "data" / "outputs" / "gta6_unique_long_shorts"
output_dir.mkdir(parents=True, exist_ok=True)
storage = ProductionStorage()

print("\n--- Initializing Whisper Model (base / cpu / int8) ---", flush=True)
whisper_model = WhisperModel("base", device="cpu", compute_type="int8")

# Unique, non-overlapping long clips (45s to 90s)
clips_data = [
    {
        "id": "gta6_unique_74s_vice_city_journey",
        "source": ROOT / "data" / "live_buffers" / "tgg_gta6" / "stream_part2.mp4",
        "start": 11.0,
        "end": 85.0,  # 74 seconds (1m 14s)
        "hook": "VICE CITY FIRST LOOK & DRIP! 🌴🔥",
        "title": "GTA 6 First Trip To Vice City: New Drip & Mini-Game! 🌴🏀 #shorts",
        "description": "TGG gets the fresh Vice City outfit and hits the first mini-game trickshot in GTA 6! The vibe is unreal. Subscribe for daily viral GTA 6 gameplay clips!\n\n#shorts #GTA6 #ViceCity #gaming #TGG #viral #RockstarGames",
        "tags": ["shorts", "GTA6", "ViceCity", "gaming", "TGG", "viral", "RockstarGames"],
        "category_id": "20",  # Gaming
    },
    {
        "id": "gta6_unique_56s_cops_and_city",
        "source": ROOT / "data" / "live_buffers" / "tgg_gta6" / "stream_part1.mp4",
        "start": 11.0,
        "end": 67.0,  # 56 seconds
        "hook": "GTA 6 POLICE AI IS TERRIFYING! 😱🚔",
        "title": "GTA 6 Cops Are Harder Than GTA 5! Full Police Chase & Vice City Rush 😱 #shorts",
        "description": "GTA 6 cops are on another level! TGG barely escapes the police AI and heads straight to Vice City. Subscribe for more crazy GTA 6 highlights!\n\n#shorts #GTA6 #gaming #policechase #TGG #viral #rockstar",
        "tags": ["shorts", "GTA6", "gaming", "policechase", "TGG", "viral", "rockstar"],
        "category_id": "20",
    },
    {
        "id": "gta6_unique_48s_toll_booth_shock",
        "source": ROOT / "data" / "live_buffers" / "tgg_gta6" / "stream_part3.mp4",
        "start": 7.0,
        "end": 55.0,  # 48 seconds
        "hook": "GTA 6 MADE ME PAY 5 BUCKS?! 💀💵",
        "title": "Rockstar Added Toll Booths To GTA 6?! Hilarious Toll Reaction 💀💵 #shorts",
        "description": "Five bucks for a toll booth in this economy?! TGG discovers Rockstar added toll booths in GTA 6. Subscribe for daily GTA 6 clips!\n\n#shorts #GTA6 #gaming #funny #TGG #viral #GrandTheftAuto",
        "tags": ["shorts", "GTA6", "gaming", "funny", "TGG", "viral", "GrandTheftAuto"],
        "category_id": "20",
    }
]

published_results = []
quota_reached = False

for i, clip in enumerate(clips_data, 1):
    clip_id = clip["id"]
    src_file = clip["source"]
    start = clip["start"]
    end = clip["end"]
    duration = end - start
    title = clip["title"]
    hook = clip["hook"]
    
    print(f"\n=======================================================", flush=True)
    print(f"[{i}/{len(clips_data)}] 🎬 PROCESSING: {title}", flush=True)
    print(f"Source: {src_file.name} | Duration: {duration:.1f}s ({start:.1f}s - {end:.1f}s)", flush=True)
    print(f"=======================================================", flush=True)
    
    # 1. Cut audio slice for Whisper
    wav_path = output_dir / f"{clip_id}_audio.wav"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-y",
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-i", str(src_file),
        "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(wav_path)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # 2. Transcribe word-level timestamps
    print("Transcribing dialogue with word timestamps...", flush=True)
    segments, _ = whisper_model.transcribe(
        str(wav_path),
        language="en",
        condition_on_previous_text=False,
        vad_filter=True,
        word_timestamps=True
    )
    
    rows = []
    spoken_words = []
    for s in segments:
        word_list = []
        if getattr(s, "words", None):
            for w in s.words:
                clean_w = str(w.word).strip()
                if clean_w:
                    word_list.append({
                        "word": clean_w,
                        "start": round(start + float(w.start), 2),
                        "end": round(start + float(w.end), 2)
                    })
                    spoken_words.append(clean_w)
        abs_s_start = round(start + float(s.start), 2)
        abs_s_end = round(start + float(s.end), 2)
        rows.append(Segment(abs_s_start, abs_s_end, s.text.strip(), tuple(word_list)))
    
    transcript_str = " ".join(spoken_words)
    print(f"Dialogue: \"{transcript_str[:70]}...\" ({len(spoken_words)} words)", flush=True)
    
    # 3. Render 9:16 Short with Kinetic Yellow captions
    target_mp4 = output_dir / f"{clip_id}_vertical_9x16.mp4"
    thumb_path = output_dir / f"{clip_id}_thumb.jpg"
    
    cand = Candidate(
        start=start,
        end=end,
        title=hook,
        reason="GTA 6 Extended Highlight",
        score=99.0,
        transcript=transcript_str
    )
    
    if not target_mp4.exists() or target_mp4.stat().st_size == 0:
        print(f"Rendering 9:16 Vertical Video (FIT_BLUR + Kinetic Captions, {duration:.1f}s)...", flush=True)
        render(
            source=src_file,
            rows=rows,
            clip=cand,
            target=target_mp4,
            layout="FIT_BLUR",
            caption_style="KINETIC_YELLOW",
            show_hook_header=True
        )
    else:
        print(f"Using already rendered video: {target_mp4.name}", flush=True)
    
    # Extract thumbnail
    if not thumb_path.exists() or thumb_path.stat().st_size == 0:
        subprocess.run([
            "ffmpeg", "-hide_banner", "-y",
            "-ss", str(round(duration / 2.0, 2)),
            "-i", str(target_mp4),
            "-vframes", "1",
            "-q:v", "2",
            str(thumb_path)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    sha256 = compute_file_sha256(target_mp4)
    item_id = f"item_{clip_id}_{uuid.uuid4().hex[:6]}"
    
    # Save to production storage
    content_item = ContentItem(
        id=item_id,
        source_clip_id=clip_id,
        title=title,
        title_options=[title, f"INSANE: {title}", f"Watch What Happens in GTA 6! 🔥"],
        description=clip["description"],
        hashtags=clip["tags"],
        video_path_vertical=str(target_mp4),
        video_path_horizontal=str(src_file),
        thumbnail_path=str(thumb_path),
        duration_seconds=round(duration, 2),
        dialogue_quality_score=95.0,
        sha256_hash=sha256,
        created_at=datetime.now(timezone.utc).isoformat()
    )
    storage.save_content_item(content_item)
    print(f"Stored item in Production DB: {item_id}", flush=True)
    
    # Schedule record
    sched_id = f"sched_{clip_id}_{uuid.uuid4().hex[:6]}"
    schedule_rec = PublishSchedule(
        id=sched_id,
        item_id=item_id,
        platform=PlatformType.YOUTUBE_SHORTS.value,
        scheduled_time_iso=datetime.now(timezone.utc).isoformat(),
        privacy_status=PrivacyStatus.PUBLIC.value,
        status=PublishStatus.SCHEDULED.value,
        rights_confirmed=True,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat()
    )
    storage.create_schedule(schedule_rec)
    
    # 4. Attempt upload
    if not quota_reached:
        print(f"Uploading to YouTube as PUBLIC: '{title}'...", flush=True)
        try:
            res = upload_public_video(
                video_path=target_mp4,
                title=title[:100],
                description=clip["description"],
                tags=clip["tags"],
                category_id=clip["category_id"],
                privacy_status="public"
            )
            video_id = res.get("video_id")
            yt_url = f"https://youtube.com/shorts/{video_id}" if video_id else res.get("url")
            print(f"✅ UPLOAD SUCCESSFUL! Published URL: {yt_url}", flush=True)
            
            # Update DB schedule
            schedule_rec.status = PublishStatus.PUBLISHED.value
            schedule_rec.external_id = video_id
            schedule_rec.external_url = yt_url
            schedule_rec.published_at = datetime.now(timezone.utc).isoformat()
            storage.update_schedule(schedule_rec)
            
            published_results.append({
                "clip_id": clip_id,
                "title": title,
                "duration": f"{duration:.1f}s",
                "status": "PUBLISHED_PUBLIC",
                "video_id": video_id,
                "url": yt_url,
                "file": str(target_mp4)
            })
            
            # Wait 5 seconds between uploads
            time.sleep(5)
            
        except Exception as exc:
            err_msg = str(exc)
            print(f"⚠️ YouTube Upload Exception: {err_msg}", flush=True)
            if "uploadLimitExceeded" in err_msg or "quotaExceeded" in err_msg:
                print("🛑 YouTube Daily Upload Limit Reached!", flush=True)
                quota_reached = True
                schedule_rec.status = PublishStatus.SCHEDULED.value
                schedule_rec.error_message = f"Daily upload limit active: {err_msg}"
                storage.update_schedule(schedule_rec)
                published_results.append({
                    "clip_id": clip_id,
                    "title": title,
                    "duration": f"{duration:.1f}s",
                    "status": "QUEUED_QUOTA_REACHED",
                    "file": str(target_mp4),
                    "note": "Rendered & Scheduled in calendar"
                })
            else:
                schedule_rec.status = PublishStatus.FAILED.value
                schedule_rec.error_message = err_msg
                storage.update_schedule(schedule_rec)
                published_results.append({
                    "clip_id": clip_id,
                    "title": title,
                    "duration": f"{duration:.1f}s",
                    "status": "FAILED",
                    "error": err_msg
                })
    else:
        print(f"Daily quota limit active. Video rendered and queued: '{title}'", flush=True)
        schedule_rec.status = PublishStatus.SCHEDULED.value
        schedule_rec.error_message = "Daily upload limit active. Scheduled in calendar."
        storage.update_schedule(schedule_rec)
        published_results.append({
            "clip_id": clip_id,
            "title": title,
            "duration": f"{duration:.1f}s",
            "status": "QUEUED_QUOTA_REACHED",
            "file": str(target_mp4),
            "note": "Rendered & Scheduled in calendar"
        })

print("\n" + "=" * 65, flush=True)
print("📊 FINAL PUBLISHING SUMMARY:", flush=True)
print(json.dumps(published_results, indent=2), flush=True)
print("=" * 65, flush=True)
