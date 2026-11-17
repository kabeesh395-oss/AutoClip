import json
import os
import sys
import time
from pathlib import Path
import subprocess

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

output_dir = ROOT / "data" / "outputs" / "ksi_more_shorts"
output_dir.mkdir(parents=True, exist_ok=True)

whisper_model = WhisperModel("base", device="cpu", compute_type="int8")

video_url = "https://youtu.be/jlV9kJDdYug"

clips = [
    {
        "index": 1,
        "cut_start": 164.0,
        "cut_end": 196.0,
        "title": "BRO THOUGHT HIS UKULELE WAS A GUITAR?! 💀😭",
        "description": "Contestant walked in with a ukulele claiming it's a guitar and wanted to sing a Morpheus tribute! 💀😭 KSI was in tears!\n\nSubscribe for more daily hilarious moments!\n\n#Shorts #KSI #AdamW #Funny #Comedy #ViralShorts #TryNotToLaugh #Trending",
        "tags": ["Shorts", "KSI", "AdamW", "Funny", "Comedy", "ViralShorts", "Trending", "Guitar"],
        "hook": "UKULELE GUITAR ROAST 💀"
    },
    {
        "index": 2,
        "cut_start": 384.0,
        "cut_end": 414.0,
        "title": "HE ROASTED KSI'S FOREHEAD IN FRONT OF EVERYONE! 💀😭",
        "description": "'Your forehead so big it needed a visa!' 💀😭 Bro walked up and roasted KSI directly to his face!\n\nLike and subscribe for more viral try-not-to-laugh clips!\n\n#Shorts #KSI #ForeheadRoast #Comedy #FunnyShorts #Trending #Roast",
        "tags": ["Shorts", "KSI", "Roast", "Comedy", "Funny", "Trending", "ViralShorts"],
        "hook": "FOREHEAD NEEDED A VISA 💀"
    },
    {
        "index": 3,
        "cut_start": 1183.0,
        "cut_end": 1211.0,
        "title": "THE JOKE THAT BROKE KSI AND ADAM W! 💀😭",
        "description": "This out-of-pocket dark joke broke KSI and Adam W instantly! 💀😭 You will not believe this punchline!\n\nDid you laugh? Comment below! Subscribe for more.\n\n#Shorts #KSI #AdamW #DarkHumor #Comedy #TryNotToLaugh #Viral",
        "tags": ["Shorts", "KSI", "AdamW", "Comedy", "Funny", "DarkHumor", "ViralShorts"],
        "hook": "THEY COULD NOT HOLD IT 😭"
    }
]

uploaded = []

for c in clips:
    idx = c["index"]
    cs, ce = c["cut_start"], c["cut_end"]
    clip_title = c["title"]
    hook_hdr = c["hook"]
    
    print(f"\n==================================================", flush=True)
    print(f"🎬 [CLIP {idx}/3] {clip_title}", flush=True)
    print(f"Section: {cs}s -> {ce}s ({ce - cs:.1f}s)", flush=True)
    print(f"==================================================", flush=True)
    
    raw_seg_path = output_dir / f"raw_seg_{idx}.mp4"
    if not raw_seg_path.exists() or raw_seg_path.stat().st_size < 1000:
        print(f"Downloading section {cs}-{ce} with yt-dlp...", flush=True)
        # Download 4s buffer before and after for clean keyframes
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
    
    # Calculate offset within downloaded segment
    # Get duration of downloaded segment
    probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(raw_seg_path)]
    raw_dur = float(subprocess.check_output(probe_cmd).decode().strip())
    
    target_dur = ce - cs
    # We padded by 3s at start
    start_offset = 3.0 if raw_dur > target_dur + 2 else 0.0
    
    subprocess.run([
        "ffmpeg", "-hide_banner", "-y",
        "-ss", f"{start_offset:.3f}", "-t", f"{target_dur:.3f}",
        "-i", str(raw_seg_path),
        "-c", "copy",
        str(cut_mp4)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    subprocess.run([
        "ffmpeg", "-hide_banner", "-y",
        "-i", str(cut_mp4),
        "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(wav_path)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Transcribe
    print("Transcribing with Whisper...", flush=True)
    segments, _ = whisper_model.transcribe(str(wav_path), language="en", vad_filter=True, word_timestamps=True)
    
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
    
    # Render 9:16
    rendered_mp4 = output_dir / f"short_{idx}_rendered.mp4"
    cand = Candidate(
        start=0.0,
        end=target_dur,
        title=hook_hdr,
        reason="Viral Roast Moment",
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
    
    # Upload to YouTube
    print(f"Uploading to YouTube: '{clip_title}'...", flush=True)
    try:
        res = upload_public_video(
            video_path=rendered_mp4,
            title=clip_title[:100],
            description=c["description"],
            tags=c["tags"],
            category_id="23"
        )
        vid = res.get("video_id")
        y_url = f"https://youtube.com/shorts/{vid}" if vid else "Uploaded"
        print(f"✅ UPLOAD SUCCESS: {y_url}", flush=True)
        uploaded.append({"title": clip_title, "url": y_url, "id": vid, "status": "LIVE"})
    except Exception as e:
        print(f"⚠️ Upload notice/error: {e}", flush=True)
        uploaded.append({"title": clip_title, "file": str(rendered_mp4), "status": f"Pending: {e}"})
    
    time.sleep(3)

print("\n" + "=" * 60, flush=True)
print("🎉 COMPLETED 3 ADDITIONAL SHORTS FOR VIDEO 2! 🎉", flush=True)
print(json.dumps(uploaded, indent=2), flush=True)
print("=" * 60, flush=True)
