import subprocess
from pathlib import Path
from faster_whisper import WhisperModel

url = "https://www.youtube.com/live/9-W_wdkBmAA"
temp_dir = Path("AutoClip/data/kai_probe")
temp_dir.mkdir(parents=True, exist_ok=True)

model = WhisperModel("base", device="cpu", compute_type="int8")

probes = [
    (120, 180, "Kai Intro & Marathon Hype"),
    (480, 540, "Prologue Heist Chaos"),
    (1620, 1680, "First Car Drive / Lamar"),
    (2160, 2220, "Lamar Roasting Scene"),
    (3600, 3660, "Mission Crashout"),
]

for s, e, label in probes:
    wav_path = temp_dir / f"kai_{s}_{e}.wav"
    if not wav_path.exists():
        print(f"\nFetching {label} [{s}s - {e}s]...", flush=True)
        # Download audio section directly
        cmd = [
            "AutoClip/.venv/Scripts/python.exe", "-m", "yt_dlp",
            "--download-sections", f"*{s}-{e}",
            "-f", "140/251/bestaudio",
            "-o", str(temp_dir / f"raw_{s}_{e}.%(ext)s"),
            url
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Convert to 16kHz mono wav
        for f in temp_dir.glob(f"raw_{s}_{e}.*"):
            subprocess.run([
                "ffmpeg", "-hide_banner", "-y",
                "-i", str(f),
                "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                str(wav_path)
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            f.unlink()
            break
            
    if wav_path.exists():
        print(f"=== {label} ===", flush=True)
        segs, _ = model.transcribe(str(wav_path), language="en", vad_filter=True)
        for seg in segs:
            print(f"[{s + seg.start:.1f}s -> {s + seg.end:.1f}s] {seg.text}", flush=True)
