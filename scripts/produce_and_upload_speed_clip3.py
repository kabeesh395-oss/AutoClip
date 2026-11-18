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
logger = logging.getLogger("speed_producer_3")

print("=" * 65, flush=True)
print("🚀 PRODUCING & UPLOADING CLIP 3 TO TEST MAX UPLOAD CAPACITY", flush=True)
print("=" * 65, flush=True)

output_dir = ROOT / "data" / "outputs" / "speed_gta6_shorts"
output_dir.mkdir(parents=True, exist_ok=True)
storage = ProductionStorage()

whisper_model = WhisperModel("base", device="cpu", compute_type="int8")

clip = {
    "id": "speed_gta6_clip_3_safahouse",
    "source": ROOT / "data" / "live_buffers" / "speed_gta6" / "speed_part4.mp4",
    "start": 2.0,
    "end": 52.0,  # 50.0 seconds (~1 min)
    "hook": "SPEED TESTING GTA 6 SAFE HOUSE! 💀🏠",
    "title": "IShowSpeed Tests GTA 6 Safehouse Physics: \"WHAT ARE YOU DOING?!\" 💀😭 #shorts",
    "description": "IShowSpeed tests the item drop physics in the GTA 6 safehouse and completely breaks character! 'Let's put it to the test! No go to sleep!'\n\n#shorts #IShowSpeed #GTA6 #gaming #Speed #viral #funnygameplay #rockstargames",
    "tags": ["shorts", "IShowSpeed", "GTA6", "gaming", "Speed", "viral", "funnygameplay", "rockstargames"],
    "category_id": "20",
}

clip_id = clip["id"]
src_file = clip["source"]
start = clip["start"]
end = clip["end"]
duration = end - start
title = clip["title"]
hook = clip["hook"]

print(f"🎬 PROCESSING: {title} ({duration:.1f}s)", flush=True)

# 1. Cut audio slice
wav_path = output_dir / f"{clip_id}_audio.wav"
subprocess.run([
    "ffmpeg", "-hide_banner", "-y",
    "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
    "-i", str(src_file),
    "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
    str(wav_path)
], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# 2. Transcribe word-level timestamps
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
print(f"Dialogue: \"{transcript_str[:70]}...\"", flush=True)

# 3. Render 9:16 Short with Kinetic Yellow captions
target_mp4 = output_dir / f"{clip_id}_vertical_9x16.mp4"
thumb_path = output_dir / f"{clip_id}_thumb.jpg"

cand = Candidate(
    start=start,
    end=end,
    title=hook,
    reason="IShowSpeed Live Highlight",
    score=99.0,
    transcript=transcript_str
)

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

# Extract thumbnail
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

content_item = ContentItem(
    id=item_id,
    source_clip_id=clip_id,
    title=title,
    title_options=[title, f"INSANE: {title}", f"IShowSpeed GTA 6 Safehouse Moment 🔥"],
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
    schedule_rec.status = PublishStatus.PUBLISHED.value
    schedule_rec.external_id = video_id
    schedule_rec.external_url = yt_url
    schedule_rec.published_at = datetime.now(timezone.utc).isoformat()
    storage.update_schedule(schedule_rec)
except Exception as exc:
    err_msg = str(exc)
    print(f"⚠️ YouTube Upload Exception: {err_msg}", flush=True)
    if "uploadLimitExceeded" in err_msg or "quotaExceeded" in err_msg:
        print("🛑 Maximum daily upload capacity reached!", flush=True)
        schedule_rec.status = PublishStatus.SCHEDULED.value
        schedule_rec.error_message = f"Daily upload limit reached: {err_msg}"
        storage.update_schedule(schedule_rec)
