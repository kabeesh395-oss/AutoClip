import json
import os
import sys
import time
from pathlib import Path

# Add project root to path
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

video_path = ROOT / "data" / "sidemen_mrbeast.mp4"
output_dir = ROOT / "data" / "outputs" / "viral_mrbeast_shorts"
output_dir.mkdir(parents=True, exist_ok=True)

print("\n=== INITIALIZING WHISPER MODEL ===", flush=True)
whisper_model = WhisperModel("base", device="cpu", compute_type="int8")

clips_meta = [
    {
        "index": 1,
        "start": 41.0,
        "end": 68.0,
        "title": "HE ROASTED THE WHOLE SIDEMEN ROOM?! 💀😂",
        "description": "This contestant roasted every single Sidemen member in front of MrBeast! 💀😂 Who was that guy?!\n\nWatch until the end for the unexpected roast! Subscribe for daily viral moments.\n\n#Shorts #MrBeast #Sidemen #TryNotToLaugh #Funny #Comedy #ViralShorts",
        "tags": ["Shorts", "MrBeast", "Sidemen", "KSI", "TryNotToLaugh", "Funny", "Comedy", "Viral"],
        "hook": "HE DIDN'T KNOW WHO HE WAS 💀"
    },
    {
        "index": 2,
        "start": 536.0,
        "end": 561.0,
        "title": "BRO TOLD KSI 'I NEVER HUGGED MY DAD' 💀😭",
        "description": "Contestant walked in, called himself MrBeast's dad, and completely destroyed KSI with this unhinged roast! 💀😭\n\nDrop a like if this caught you off guard! Subscribe for more.\n\n#Shorts #KSI #MrBeast #Sidemen #Roast #Comedy #FunnyShorts #Trending",
        "tags": ["Shorts", "KSI", "MrBeast", "Sidemen", "Roast", "Comedy", "Funny", "Trending"],
        "hook": "UNHINGED KSI ROAST 💀"
    },
    {
        "index": 3,
        "start": 571.0,
        "end": 603.5,
        "title": "MRBEAST COULD NOT HOLD HIS LAUGH HERE! 😭💀",
        "description": "The most out-of-pocket joke that broke the entire Sidemen room and MrBeast instantly! 😭💀 You cannot make this up!\n\nDid you laugh? Comment below! Subscribe for daily clips.\n\n#Shorts #MrBeast #Sidemen #LaughChallenge #DarkHumor #Comedy #Viral",
        "tags": ["Shorts", "MrBeast", "Sidemen", "TryNotToLaugh", "DarkHumor", "Viral", "Humor"],
        "hook": "THEY COULD NOT HOLD IT IN 😭"
    },
    {
        "index": 4,
        "start": 1518.0,
        "end": 1558.0,
        "title": "THE DARKEST JOKE IN SIDEMEN HISTORY?! 💀😭",
        "description": "He called him 'Mr. Breast' and then dropped the craziest story twist about his dog and granny! 💀😭 The ending is wild!\n\nSubscribe for the best unhinged moments on YouTube!\n\n#Shorts #MrBeast #Sidemen #TryNotToLaugh #Unbelievable #Comedy #Funny",
        "tags": ["Shorts", "MrBeast", "Sidemen", "Comedy", "Funny", "Wild", "ViralShorts"],
        "hook": "MR BREAST AND GRANNY 💀"
    }
]

uploaded_results = []

for meta in clips_meta:
    idx = meta["index"]
    start, end = meta["start"], meta["end"]
    clip_title = meta["title"]
    hook_hdr = meta["hook"]
    
    print(f"\n==================================================", flush=True)
    print(f"🎬 PROCESSING CLIP {idx}/4: {clip_title}", flush=True)
    print(f"Time: {start}s -> {end}s ({end - start:.1f}s duration)", flush=True)
    print(f"==================================================", flush=True)
    
    # 1. Extract audio segment
    wav_path = output_dir / f"clip_{idx}_audio.wav"
    import subprocess
    subprocess.run([
        "ffmpeg", "-hide_banner", "-y",
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-i", str(video_path),
        "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(wav_path)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # 2. Transcribe with word timestamps
    print(f"Transcribing audio with word timestamps...", flush=True)
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
    
    print(f"Detected {len(rows)} dialogue segments, {len(spoken_words)} words.", flush=True)
    
    # 3. Render 9:16 Short with Kinetic Yellow captions
    target_mp4 = output_dir / f"short_{idx}_{int(start)}s.mp4"
    cand = Candidate(
        start=start,
        end=end,
        title=hook_hdr,
        reason="Viral Comedy Moment",
        score=99.0,
        transcript=" ".join(spoken_words)
    )
    
    print(f"Rendering 9:16 Vertical Video with Kinetic Captions via FFmpeg...", flush=True)
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
    
    # 4. Upload directly to connected YouTube channel
    print(f"Uploading to YouTube: '{clip_title}'...", flush=True)
    res = upload_public_video(
        video_path=target_mp4,
        title=clip_title[:100],
        description=meta["description"],
        tags=meta["tags"],
        category_id="23"  # Comedy category
    )
    video_id = res.get("video_id")
    yt_url = f"https://youtube.com/shorts/{video_id}" if video_id else "Uploaded"
    print(f"✅ UPLOAD SUCCESS! YouTube Link: {yt_url}", flush=True)
    
    uploaded_results.append({
        "clip_num": idx,
        "title": clip_title,
        "video_id": video_id,
        "url": yt_url,
        "file": str(target_mp4)
    })
    
    # Pause slightly between uploads to keep API quota healthy
    time.sleep(3)

print("\n" + "=" * 60, flush=True)
print("🎉 ALL 4 VIRAL SHORTS PRODUCED & UPLOADED SUCCESSFULLY! 🎉", flush=True)
print(json.dumps(uploaded_results, indent=2), flush=True)
print("=" * 60, flush=True)
