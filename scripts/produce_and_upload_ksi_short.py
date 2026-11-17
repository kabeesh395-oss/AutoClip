import json
import os
import sys
from pathlib import Path

ROOT = Path("AutoClip").resolve()
sys.path.insert(0, str(ROOT))

from faster_whisper import WhisperModel
from autoclip_app.pipeline import Candidate, Segment, render
from autoclip_app.youtube import upload_public_video, youtube_status

print("=== CHECKING YOUTUBE CONNECTION ===", flush=True)
yt_stat = youtube_status()
print(yt_stat, flush=True)
if not yt_stat.get("connected"):
    raise RuntimeError("YouTube channel is not connected!")

video_path = ROOT / "data" / "ksi_section.mp4"
output_dir = ROOT / "data" / "outputs" / "viral_mrbeast_shorts"
output_dir.mkdir(parents=True, exist_ok=True)

# Start around 2.0s (where the joke kicks off: "To deal with this situation, you are finished, you are dead")
# End around 50.5s ("I am untied!")
start_time = 1.90
end_time = 50.20
clip_title = "HOW TO ESCAPE A HOSTAGE SITUATION?! 😭💀"
hook_hdr = "HOW TO ESCAPE A HOSTAGE 💀"

print(f"\n==================================================", flush=True)
print(f"🎬 PRODUCING REQUESTED SHORT: {clip_title}", flush=True)
print(f"Clip Range in source: {start_time:.2f}s -> {end_time:.2f}s (Duration: {end_time - start_time:.1f}s)", flush=True)
print(f"==================================================", flush=True)

# 1. Transcribe with word timestamps
print("Transcribing audio with Whisper...", flush=True)
whisper_model = WhisperModel("base", device="cpu", compute_type="int8")

wav_path = output_dir / "ksi_hostage_audio.wav"
import subprocess
subprocess.run([
    "ffmpeg", "-hide_banner", "-y",
    "-ss", f"{start_time:.3f}", "-to", f"{end_time:.3f}",
    "-i", str(video_path),
    "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
    str(wav_path)
], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

segments, _ = whisper_model.transcribe(
    str(wav_path),
    language="en",
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
                    "start": round(start_time + float(w.start), 2),
                    "end": round(start_time + float(w.end), 2)
                })
                spoken_words.append(clean_w)
    abs_s_start = round(start_time + float(s.start), 2)
    abs_s_end = round(start_time + float(s.end), 2)
    rows.append(Segment(abs_s_start, abs_s_end, s.text.strip(), tuple(word_list)))

print(f"Transcription complete: {len(rows)} segments, {len(spoken_words)} words.", flush=True)

# 2. Render 9:16 Vertical Video with Kinetic Captions
target_mp4 = output_dir / "short_ksi_hostage_escape.mp4"
cand = Candidate(
    start=start_time,
    end=end_time,
    title=hook_hdr,
    reason="Requested 45s-90s Hostage Comedy Scene",
    score=99.0,
    transcript=" ".join(spoken_words)
)

print("Rendering 9:16 Vertical Video with Kinetic Captions via FFmpeg...", flush=True)
render(
    source=video_path,
    rows=rows,
    clip=cand,
    target=target_mp4,
    layout="FIT_BLUR",
    caption_style="KINETIC_YELLOW",
    show_hook_header=True
)

size_mb = target_mp4.stat().st_size / (1024 * 1024)
print(f"Rendered successfully: {target_mp4.name} ({size_mb:.2f} MB)", flush=True)

# 3. Upload directly to connected YouTube channel
print(f"Uploading to YouTube: '{clip_title}'...", flush=True)
description = (
    "The most hilarious self-defense hostage escape in history! 😂💀 "
    "Bro told his kidnapper 'Untie me so I can save you!'\n\n"
    "Did he actually pull this off?! Drop a like and comment below!\n\n"
    "#Shorts #KSI #SelfDefense #Funny #Comedy #ViralShorts #Trending #TryNotToLaugh #AdamW"
)
tags = ["Shorts", "KSI", "Comedy", "Funny", "Hostage", "SelfDefense", "ViralShorts", "Trending", "AdamW"]

res = upload_public_video(
    video_path=target_mp4,
    title=clip_title[:100],
    description=description,
    tags=tags,
    category_id="23"
)

video_id = res.get("video_id")
yt_url = f"https://youtube.com/shorts/{video_id}" if video_id else "Uploaded"
print("\n" + "=" * 60, flush=True)
print(f"🎉 UPLOAD SUCCESS! YouTube Link: {yt_url}", flush=True)
print(f"Video ID: {video_id}", flush=True)
print(f"Local file: {target_mp4}", flush=True)
print("=" * 60, flush=True)
