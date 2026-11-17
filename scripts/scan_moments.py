import subprocess
from pathlib import Path
from faster_whisper import WhisperModel

video_path = Path("AutoClip/data/sidemen_mrbeast.mp4")
temp_dir = Path("AutoClip/data/temp_audio")
temp_dir.mkdir(parents=True, exist_ok=True)

model = WhisperModel("base", device="cpu", compute_type="int8")

sections = [
    (28, 90, "Contestant 1 - Try Not To Laugh"),
    (370, 440, "Physical Comedy - Prank Act"),
    (535, 605, "High Energy Act - MrBeast Stunned"),
    (1490, 1560, "Dark / Absurd Comedy - Everyone Broke"),
]

for start, end, label in sections:
    print(f"\n=======================================================")
    print(f"=== {label} [{start}s - {end}s] ===")
    print(f"=======================================================")
    wav_path = temp_dir / f"seg_{start}_{end}.wav"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-y",
        "-ss", str(start), "-to", str(end),
        "-i", str(video_path),
        "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(wav_path)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    segments, _ = model.transcribe(
        str(wav_path),
        language="en",
        condition_on_previous_text=False,
        vad_filter=True,
        word_timestamps=True
    )
    for s in segments:
        abs_start = start + s.start
        abs_end = start + s.end
        print(f"[{abs_start:.2f}s -> {abs_end:.2f}s] {s.text}")
