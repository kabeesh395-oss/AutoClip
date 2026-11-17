from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from .models import SpikeEvent

logger = logging.getLogger("autoclip.chat.extractor")

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "data" / "outputs" / "chat_highlights"


class ChatClipExtractor:
    """
    Extracts high-energy video highlights corresponding to detected chat spikes
    using FFmpeg.
    """

    def __init__(self, output_dir: Path | str = OUTPUT_DIR) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def extract_clip(
        self,
        source_video_path: Path | str,
        spike: SpikeEvent,
        reencode: bool = False,
    ) -> Path:
        """
        Trims the video segment [spike.clip_start, spike.clip_end] and writes
        the extracted clip to output_dir.
        """
        source = Path(source_video_path)
        if not source.exists():
            raise FileNotFoundError(f"Source video file not found: {source}")

        out_name = f"{spike.spike_id}.mp4"
        out_path = self.output_dir / out_name

        start_sec = max(0.0, spike.clip_start)
        duration_sec = max(1.0, spike.clip_end - start_sec)

        # Fast seek before input, accurate duration after
        if not reencode:
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(round(start_sec, 3)),
                "-i", str(source),
                "-t", str(round(duration_sec, 3)),
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                str(out_path),
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(round(start_sec, 3)),
                "-i", str(source),
                "-t", str(round(duration_sec, 3)),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-c:a", "aac", "-b:a", "192k",
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                str(out_path),
            ]

        logger.info("Executing FFmpeg clip extraction: %s", " ".join(cmd))
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            # Fallback to re-encode if stream copy failed
            if not reencode:
                logger.warning("Stream copy failed; falling back to re-encoding...")
                return self.extract_clip(source, spike, reencode=True)
            logger.error("FFmpeg error: %s", result.stderr)
            raise RuntimeError(f"FFmpeg extraction failed (exit {result.returncode}): {result.stderr[-300:]}")

        spike.clip_path = str(out_path)
        spike.status = "EXTRACTED"

        # Generate thumbnail at peak time
        self.generate_thumbnail(source, spike)
        return out_path

    def generate_thumbnail(self, source_video_path: Path | str, spike: SpikeEvent) -> Path | None:
        """Captures a video thumbnail at the exact peak hype timestamp."""
        source = Path(source_video_path)
        thumb_path = self.output_dir / f"{spike.spike_id}_thumb.jpg"
        peak_t = max(0.0, spike.peak_time)

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(round(peak_t, 3)),
            "-i", str(source),
            "-vframes", "1",
            "-q:v", "2",
            str(thumb_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode == 0 and thumb_path.exists():
            return thumb_path
        return None
