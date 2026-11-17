import json
import os
import sys
import time
from pathlib import Path
import subprocess
from datetime import datetime, timedelta, timezone

ROOT = Path("AutoClip").resolve()
sys.path.insert(0, str(ROOT))

from faster_whisper import WhisperModel
from autoclip_app.pipeline import Candidate, Segment, render
from autoclip_app.youtube import upload_public_video, schedule_youtube_video, youtube_status

print("=== CHECKING YOUTUBE CONNECTION ===", flush=True)
yt_stat = youtube_status()
print(yt_stat, flush=True)
if not yt_stat.get("connected"):
    raise RuntimeError("YouTube channel is not connected!")

output_dir = ROOT / "data" / "outputs" / "kai_shorts"
output_dir.mkdir(parents=True, exist_ok=True)

whisper_model = WhisperModel("base", device="cpu", compute_type="int8")

video_url = "https://www.youtube.com/live/9-W_wdkBmAA"

clips = [
    {
        "index": 1,
        "cut_start": 41630.0,
        "cut_end": 41656.0,
        "title": "KAI CENAT: WE ARE NOT READY FOR GTA 6! 😱🎮 #Shorts",
        "description": "Kai Cenat says we are definitely not ready for GTA 6 after looking at GTA 5 graphics! Do you agree? 🎮💀\n\nDrop a like and subscribe for daily streamer clips!\n\n#KaiCenat #GTA6 #GTA5 #Gaming #Shorts #TheKM #Viral #Trending",
        "tags": ["Shorts", "Kai Cenat", "GTA 6", "GTA 5", "Gaming", "Viral", "TheKM", "Rockstar Games", "Trending"],
        "hook": "WE ARE NOT READY FOR GTA 6 😱",
        "schedule_hours": 3
    },
    {
        "index": 2,
        "cut_start": 41021.0,
        "cut_end": 41041.0,
        "title": "TREVOR'S MOST UNHINGED OUT-OF-POCKET MOMENT?! 💀😭 #Shorts",
        "description": "Kai Cenat's reaction to Trevor Phillips saying he needs to meditate or masturbate in GTA 5 is priceless! 💀😭\n\nSubscribe for the funniest GTA moments!\n\n#Shorts #KaiCenat #GTA5 #TrevorPhilips #Funny #Gaming #Meme #Trending",
        "tags": ["Shorts", "Kai Cenat", "GTA 5", "Trevor Phillips", "Funny Moments", "Gaming", "Viral", "Meme", "Trending"],
        "hook": "TREVOR IS UNHINGED 💀",
        "schedule_hours": 6
    },
    {
        "index": 3,
        "cut_start": 126.0,
        "cut_end": 144.5,
        "title": "KAI CENAT GIVES AWAY 3 PS5s ON GTA 5 STREAM?! 🎮🔥 #Shorts",
        "description": "Kai Cenat announcing a 3x PS5 giveaway live during his GTA 5 marathon stream! 🎮🔥\n\nSubscribe for more legendary stream moments!\n\n#Shorts #KaiCenat #GTA5 #PS5 #Giveaway #Gaming #TheKM #Viral #Trending",
        "tags": ["Shorts", "Kai Cenat", "GTA 5", "PS5", "Giveaway", "Gaming", "TheKM", "Viral", "Trending"],
        "hook": "3x PS5 GIVEAWAY LIVE 🎮🔥",
        "schedule_hours": 9
    }
]

uploaded = []

for c in clips:
    idx = c["index"]
    cs = c["cut_start"]
    ce = c["cut_end"]
    clip_title = c["title"]
    hook_hdr = c["hook"]
    target_dur = ce - cs
    
    print("\n" + "=" * 60, flush=True)
    print(f"[{idx}/3] Processing: {clip_title}", flush=True)
    print(f"Timestamp: {cs}s - {ce}s ({target_dur:.1f}s)", flush=True)
    
    raw_seg_path = output_dir / f"raw_seg_{idx}.mp4"
    if not raw_seg_path.exists() or raw_seg_path.stat().st_size < 1000:
        print(f"Downloading section {cs}-{ce} with yt-dlp...", flush=True)
        dl_start = max(0, cs - 3)
        dl_end = ce + 3
        dl_cmd = [
            sys.executable, "-m", "yt_dlp",
            "--download-sections", f"*{dl_start:.1f}-{dl_end:.1f}",
            "--force-keyframes-at-cuts",
            "-f", "298+140-11/398+251-11/bestvideo[height<=720]+bestaudio/best",
            "--merge-output-format", "mp4",
            "-o", str(raw_seg_path),
            video_url
        ]
        subprocess.run(dl_cmd, check=True)
    
    # Precise trim with ffmpeg
    cut_mp4 = output_dir / f"cut_{idx}.mp4"
    wav_path = output_dir / f"audio_{idx}.wav"
    
    probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(raw_seg_path)]
    raw_dur = float(subprocess.check_output(probe_cmd).decode().strip())
    
    start_offset = 3.0 if raw_dur > target_dur + 2 else 0.0
    
    subprocess.run([
        "ffmpeg", "-hide_banner", "-y",
        "-ss", f"{start_offset:.3f}", "-t", f"{target_dur:.3f}",
        "-i", str(raw_seg_path),
        "-c", "copy",
        str(cut_mp4)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Extract audio for whisper
    subprocess.run([
        "ffmpeg", "-hide_banner", "-y",
        "-i", str(cut_mp4),
        "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(wav_path)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    print("Transcribing with Faster-Whisper...", flush=True)
    segments, _ = whisper_model.transcribe(
        str(wav_path),
        language="en",
        word_timestamps=True,
        vad_filter=True
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
                        "start": round(float(w.start), 2),
                        "end": round(float(w.end), 2)
                    })
                    spoken_words.append(clean_w)
        rows.append(Segment(round(float(s.start), 2), round(float(s.end), 2), s.text.strip(), tuple(word_list)))
    
    rendered_mp4 = output_dir / f"short_{idx}_rendered.mp4"
    cand = Candidate(
        start=0.0,
        end=target_dur,
        title=hook_hdr,
        reason="Viral Stream Moment",
        score=99.0,
        transcript=" ".join(spoken_words)
    )
    
    print("Rendering 9:16 Vertical Video with Kinetic Captions via FFmpeg...", flush=True)
    render(
        source=cut_mp4,
        rows=rows,
        clip=cand,
        target=rendered_mp4,
        layout="FIT_BLUR",
        caption_style="KINETIC_YELLOW",
        show_hook_header=True
    )
    
    print(f"Rendered: {rendered_mp4.name} ({rendered_mp4.stat().st_size / (1024*1024):.2f} MB)", flush=True)
    
    # Upload to YouTube (with schedule fallback if limit exceeded)
    print(f"Uploading to YouTube: '{clip_title}'...", flush=True)
    try:
        res = upload_public_video(
            video_path=rendered_mp4,
            title=clip_title[:100],
            description=c["description"],
            tags=c["tags"],
            category_id="20"  # 20 is Gaming
        )
        vid = res.get("video_id")
        y_url = f"https://youtube.com/shorts/{vid}" if vid else "Uploaded"
        print(f"✅ UPLOAD SUCCESS (PUBLIC): {y_url}", flush=True)
        uploaded.append({"title": clip_title, "url": y_url, "id": vid, "status": "LIVE"})
    except Exception as e:
        err_msg = str(e)
        print(f"⚠️ Upload limit or quota notice: {err_msg}", flush=True)
        
        # Check if quota exceeded or daily upload limit
        if "quotaExceeded" in err_msg or "uploadLimitExceeded" in err_msg or "403" in err_msg:
            print("Attempting YouTube schedule upload...", flush=True)
            sched_time = (datetime.now(timezone.utc) + timedelta(hours=c["schedule_hours"])).isoformat()
            try:
                res = schedule_youtube_video(
                    video_path=rendered_mp4,
                    title=clip_title[:100],
                    description=c["description"],
                    publish_at_iso=sched_time,
                    tags=c["tags"],
                    category_id="20"
                )
                vid = res.get("video_id")
                y_url = f"https://youtube.com/shorts/{vid}" if vid else "Scheduled"
                print(f"✅ SCHEDULED ON YOUTUBE FOR {sched_time}: {y_url}", flush=True)
                uploaded.append({"title": clip_title, "url": y_url, "id": vid, "status": f"SCHEDULED ({sched_time})"})
            except Exception as sched_err:
                print(f"YouTube schedule API also quota limited: {sched_err}", flush=True)
                uploaded.append({
                    "title": clip_title,
                    "file": str(rendered_mp4),
                    "status": "QUEUED_LOCAL_SCHEDULE",
                    "scheduled_for": sched_time,
                    "note": "Rendered & ready to publish once daily YouTube quota resets"
                })
        else:
            uploaded.append({"title": clip_title, "file": str(rendered_mp4), "status": f"Error: {err_msg}"})
    
    time.sleep(3)

print("\n" + "=" * 60, flush=True)
print("🎉 COMPLETED 3 VIRAL SHORTS FOR KAI CENAT GTA 5 STREAM! 🎉", flush=True)
print(json.dumps(uploaded, indent=2), flush=True)
print("=" * 60, flush=True)

with open(output_dir / "kai_summary.json", "w", encoding="utf-8") as f:
    json.dump(uploaded, f, indent=2)
