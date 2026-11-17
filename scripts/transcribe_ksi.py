from pathlib import Path
from faster_whisper import WhisperModel

video_path = "AutoClip/data/ksi_section.mp4"
model = WhisperModel("base", device="cpu", compute_type="int8")

segments, _ = model.transcribe(
    video_path,
    language="en",
    vad_filter=True,
    word_timestamps=True
)

for s in segments:
    print(f"[{s.start:.2f}s -> {s.end:.2f}s] {s.text}")
