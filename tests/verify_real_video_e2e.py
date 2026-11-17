"""
Stage 2 Verification: Real Video End-to-End Test
Executes real pipeline steps without mocking on an actual video file with speech audio.
"""
import json
import logging
import subprocess
from pathlib import Path
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("e2e_verify")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from autoclip_app.pipeline import (
    transcribe,
    choose_highlights,
    render,
    render_long_form,
    generate_long_form_metadata,
    generate_metadata,
    Candidate,
    Segment
)

def probe_video(video_path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration,r_frame_rate",
        "-show_entries", "format=duration,size",
        "-of", "json",
        str(video_path)
    ]
    res = subprocess.run(cmd, check=True, capture_output=True, text=True)
    data = json.loads(res.stdout)
    st = data["streams"][0] if data.get("streams") else {}
    fmt = data.get("format", {})
    dur = float(st.get("duration") or fmt.get("duration") or 0.0)
    w = int(st.get("width", 0))
    h = int(st.get("height", 0))
    return {"width": w, "height": h, "duration": dur, "size": int(fmt.get("size", 0))}

def run_real_e2e_verification():
    sample_video = ROOT / "data" / "sample_real_video.mp4"
    assert sample_video.exists(), f"Sample video {sample_video} does not exist"
    out_dir = ROOT / "data" / "e2e_stage2_output"
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=== 1. Source Video Probe ===")
    src_meta = probe_video(sample_video)
    logger.info("Source probe: %dx%d, duration: %.2fs, size: %d bytes",
                src_meta["width"], src_meta["height"], src_meta["duration"], src_meta["size"])
    assert src_meta["width"] == 1920 and src_meta["height"] == 1080, "Expected 1920x1080 source"

    logger.info("=== 2. Faster-Whisper Speech-To-Text Transcription ===")
    # Using 'tiny' or 'small' on CPU
    rows = transcribe(sample_video, model_name="tiny", language="en")
    logger.info("Whisper returned %d spoken segments:", len(rows))
    for r in rows:
        logger.info("  [%.2fs - %.2fs]: %s", r.start, r.end, r.text)
    assert len(rows) > 0, "Whisper must detect speech in the real spoken audio"

    logger.info("=== 3. Highlight Detection & Scoring ===")
    candidates = choose_highlights(rows, count=2, min_seconds=2, max_seconds=8)
    logger.info("Detected %d highlight candidate(s):", len(candidates))
    for c in candidates:
        logger.info("  Candidate: '%s' (%.2fs - %.2fs) Score: %.1f", c.title, c.start, c.end, c.score)
    assert len(candidates) > 0, "At least 1 highlight candidate must be detected"

    clip = candidates[0]

    logger.info("=== 4. 9:16 Short Video Rendering (FIT_BLUR Layout & Kinetic Captions) ===")
    short_target = out_dir / "rendered_short_9x16.mp4"
    render(
        source=sample_video,
        rows=rows,
        clip=clip,
        target=short_target,
        layout="FIT_BLUR",
        caption_style="KINETIC_YELLOW",
        show_hook_header=True
    )
    assert short_target.exists() and short_target.stat().st_size > 0, "9:16 Short must exist and be non-empty"
    short_meta = probe_video(short_target)
    logger.info("Rendered 9:16 Short: %s (%dx%d, duration: %.2fs, size: %d bytes)",
                short_target.name, short_meta["width"], short_meta["height"], short_meta["duration"], short_meta["size"])
    assert short_meta["width"] == 1080 and short_meta["height"] == 1920, f"Expected 1080x1920, got {short_meta['width']}x{short_meta['height']}"

    # Verify SRT and ASS subtitles
    srt_file = short_target.with_suffix(".srt")
    ass_file = short_target.with_suffix(".ass")
    assert srt_file.exists() and srt_file.stat().st_size > 0, "SRT subtitle file must exist"
    assert ass_file.exists() and ass_file.stat().st_size > 0, "ASS subtitle file must exist"
    logger.info("Subtitles verified: SRT (%d bytes), ASS (%d bytes)", srt_file.stat().st_size, ass_file.stat().st_size)

    logger.info("=== 5. 16:9 Long-Form Compilation Rendering ===")
    long_target = out_dir / "rendered_compilation_16x9.mp4"
    long_res = render_long_form(
        source=sample_video,
        rows=rows,
        candidates=[clip],
        target=long_target
    )
    assert long_target.exists() and long_target.stat().st_size > 0, "16:9 Long compilation must exist and be non-empty"
    long_meta = probe_video(long_target)
    logger.info("Rendered 16:9 Long Compilation: %s (%dx%d, duration: %.2fs, size: %d bytes)",
                long_target.name, long_meta["width"], long_meta["height"], long_meta["duration"], long_meta["size"])
    assert long_meta["width"] == 1920 and long_meta["height"] == 1080, f"Expected 1920x1080, got {long_meta['width']}x{long_meta['height']}"

    # Verify Chapters & Descriptions
    logger.info("Long compilation metadata: Chapters: %d, Title: '%s'",
                len(long_res.get("chapters", [])), long_res.get("youtube_title"))

    logger.info("=== 6. Thumbnail Generation ===")
    thumb_target = out_dir / "short_thumbnail.jpg"
    thumb_cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-ss", "0.5",
        "-i", str(short_target),
        "-vframes", "1",
        "-q:v", "2",
        str(thumb_target)
    ]
    subprocess.run(thumb_cmd, check=True, capture_output=True)
    assert thumb_target.exists() and thumb_target.stat().st_size > 0, "Thumbnail must exist"
    logger.info("Thumbnail generated: %s (%d bytes)", thumb_target.name, thumb_target.stat().st_size)

    # 7. Summary Report
    summary = {
        "status": "PASS",
        "sample_video": str(sample_video),
        "source": src_meta,
        "whisper_transcript": [r.text for r in rows],
        "highlight_selected": {
            "title": clip.title,
            "start": clip.start,
            "end": clip.end,
            "score": clip.score
        },
        "short_9x16": {
            "file": short_target.name,
            "width": short_meta["width"],
            "height": short_meta["height"],
            "duration": short_meta["duration"],
            "bytes": short_meta["size"]
        },
        "long_16x9": {
            "file": long_target.name,
            "width": long_meta["width"],
            "height": long_meta["height"],
            "duration": long_meta["duration"],
            "bytes": long_meta["size"]
        },
        "subtitles": {
            "srt_bytes": srt_file.stat().st_size,
            "ass_bytes": ass_file.stat().st_size
        },
        "thumbnail": {
            "file": thumb_target.name,
            "bytes": thumb_target.stat().st_size
        }
    }

    report_file = out_dir / "e2e_stage2_report.json"
    report_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info("=== ALL STAGE 2 REAL VIDEO VERIFICATION CHECKS PASSED ===")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    run_real_e2e_verification()
