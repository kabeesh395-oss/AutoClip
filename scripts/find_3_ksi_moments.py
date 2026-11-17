from pathlib import Path
from faster_whisper import WhisperModel
import subprocess

audio_path = Path("AutoClip/data/ksi_usa_audio.m4a")
model = WhisperModel("base", device="cpu", compute_type="int8")

windows = [
    (120, 190, "Window 1 (Contestant 2)"),
    (380, 460, "Window 2 (Contestant 3)"),
    (750, 830, "Window 3 (Contestant 4)"),
    (1150, 1230, "Window 4 (Contestant 5)"),
    (1450, 1530, "Window 5 (Contestant 6)"),
    (1750, 1830, "Window 6 (Contestant 7)")
]

temp_dir = Path("AutoClip/data/temp_audio2")
temp_dir.mkdir(parents=True, exist_ok=True)

for s, e, lbl in windows:
    wav = temp_dir / f"w_{s}_{e}.wav"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-y",
        "-ss", str(s), "-to", str(e),
        "-i", str(audio_path),
        "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(wav)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    print(f"\n=== {lbl} [{s}s - {e}s] ===", flush=True)
    segs, _ = model.transcribe(str(wav), language="en", vad_filter=True)
    for seg in segs:
        print(f"[{s + seg.start:.1f}s -> {s + seg.end:.1f}s] {seg.text}", flush=True)
