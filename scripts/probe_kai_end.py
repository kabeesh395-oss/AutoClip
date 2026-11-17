import subprocess
from pathlib import Path
from faster_whisper import WhisperModel

url = "https://www.youtube.com/live/9-W_wdkBmAA"
temp_dir = Path("AutoClip/data/kai_probe_end")
temp_dir.mkdir(parents=True, exist_ok=True)

model = WhisperModel("base", device="cpu", compute_type="int8")

end_probes = [
    (41000, 41060, "Final Mission Action"),
    (41800, 41860, "Killing Devin Weston / The Ending"),
    (42400, 42460, "Credits / Victory Celebration"),
    (42700, 42760, "Final Words / Signing Off")
]

for s, e, label in end_probes:
    wav_path = temp_dir / f"kai_{s}_{e}.wav"
    raw_file = temp_dir / f"raw_{s}_{e}.m4a"
    print(f"\nFetching {label} [{s}s - {e}s]...", flush=True)
    subprocess.run([
        "AutoClip/.venv/Scripts/python.exe", "-m", "yt_dlp",
        "--download-sections", f"*{s}-{e}",
        "-f", "140/251/bestaudio",
        "-o", str(raw_file),
        url
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    if raw_file.exists():
        subprocess.run([
            "ffmpeg", "-hide_banner", "-y",
            "-i", str(raw_file),
            "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(wav_path)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raw_file.unlink()
        
    if wav_path.exists():
        print(f"=== {label} ===", flush=True)
        segs, _ = model.transcribe(str(wav_path), language="en", vad_filter=True)
        for seg in segs:
            print(f"[{s + seg.start:.1f}s -> {s + seg.end:.1f}s] {seg.text}", flush=True)
