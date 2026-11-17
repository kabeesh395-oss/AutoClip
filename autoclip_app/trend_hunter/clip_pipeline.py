from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..pipeline import Candidate, Segment, generate_opus_ass, generate_viral_intelligence, transcribe
from .models import TrendHunterConfig, ViralClipRecord

logger = logging.getLogger("autoclip.trend_hunter.clip_pipeline")

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS_DIR = ROOT / "data" / "outputs" / "viral_clips"


class ViralClipPipeline:
    """
    Automatic production pipeline for detected viral live stream moments.
    Produces:
      - 9:16 vertical YouTube Shorts format (with signature FIT_BLUR layout)
      - Word-level timestamps via Faster-Whisper
      - Kinetic Opus-style electric yellow karaoke ASS subtitles
      - AI Hook, 5 viral titles, description, and hashtags
      - High-resolution thumbnail captured at peak hype timestamp
    """

    def __init__(self, output_dir: Path | None = None) -> None:
        self.output_dir = output_dir or OUTPUTS_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def process_viral_moment(
        self,
        raw_clip_path: Path,
        session_id: str,
        channel_name: str,
        platform: str,
        stream_url: str,
        peak_time: float,
        clip_start: float,
        clip_end: float,
        confidence_score: float,
        demand_score: float,
        config: TrendHunterConfig | None = None,
    ) -> ViralClipRecord:
        """
        Processes a raw extracted video segment into a publication-ready YouTube Short.
        """
        cfg = config or TrendHunterConfig()
        clip_uid = f"viral_{uuid.uuid4().hex[:8]}"
        duration = max(1.0, clip_end - clip_start)

        shorts_target = self.output_dir / f"{clip_uid}_shorts.mp4"
        thumb_target = self.output_dir / f"{clip_uid}_thumb.jpg"
        ass_target = self.output_dir / f"{clip_uid}.ass"

        # 1. Transcribe audio with Faster-Whisper
        segments: list[Segment] = []
        transcript_text = ""
        try:
            whisper_model = os.getenv("WHISPER_MODEL", "small")
            segments = transcribe(raw_clip_path, model_name=whisper_model)
            transcript_text = " ".join(s.text for s in segments).strip()
        except Exception as exc:
            logger.warning("Whisper transcription failed or no speech detected (%s); continuing with visual clip", exc)
            transcript_text = f"High-energy moment from {channel_name}'s live stream!"

        # 2. Generate Viral Metadata & Titles
        cand = Candidate(
            start=0.0,
            end=duration,
            title=f"{channel_name} Viral Moment",
            reason=f"Spike detected with {confidence_score:.0f}% confidence",
            score=confidence_score,
            transcript=transcript_text,
        )
        intel = generate_viral_intelligence(cand)
        titles = intel.get("titles") or [
            f"{channel_name} INSANE MOMENT! 😱",
            f"You Won't Believe What {channel_name} Just Did",
            f"When {channel_name} Goes Crazy Live",
        ]
        title = titles[0]
        desc = intel.get("description") or f"Best highlight from {channel_name}'s stream.\n\n#Shorts #{channel_name.replace(' ', '')}"
        tags = intel.get("tags") or ["shorts", channel_name.lower().replace(" ", ""), "viral", "streamer"]

        # 3. Generate Kinetic ASS Subtitles if speech exists
        use_ass = False
        ass_esc = ""
        if segments:
            try:
                generate_opus_ass(
                    segments,
                    clip_start=0.0,
                    clip_end=duration,
                    target_ass=ass_target,
                    caption_style=cfg.caption_style,
                    hook_title=intel.get("hook", {}).get("text") or title,
                )
                ass_esc = str(ass_target.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
                use_ass = True
            except Exception as exc:
                logger.warning("Failed to generate kinetic ASS subtitles: %s", exc)

        # 4. Render 9:16 Shorts with FIT_BLUR layout
        if cfg.layout == "9:16":
            sub_filter = f",ass='{ass_esc}'" if use_ass else ""
            vf = (
                f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                f"boxblur=25:5,eq=brightness=-0.12[bg];"
                f"[0:v]scale=1080:-1[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2{sub_filter},format=yuv420p[v]"
            )
            cmd = [
                "ffmpeg", "-hide_banner", "-y",
                "-i", str(raw_clip_path),
                "-filter_complex", vf,
                "-map", "[v]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-c:a", "aac", "-b:a", "192k",
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-movflags", "+faststart",
                str(shorts_target),
            ]
        else:
            # 16:9 widescreen layout
            sub_filter = f"ass='{ass_esc}'," if use_ass else ""
            cmd = [
                "ffmpeg", "-hide_banner", "-y",
                "-i", str(raw_clip_path),
                "-vf", f"{sub_filter}format=yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-c:a", "aac", "-b:a", "192k",
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-movflags", "+faststart",
                str(shorts_target),
            ]

        logger.info("Rendering viral Short %s: %s", clip_uid, " ".join(cmd[:10]))
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0 or not shorts_target.exists() or shorts_target.stat().st_size == 0:
            logger.warning("Complex Shorts render failed, falling back to direct scale: %s", res.stderr[-300:])
            # Fallback direct 9:16 crop
            fallback_cmd = [
                "ffmpeg", "-hide_banner", "-y",
                "-i", str(raw_clip_path),
                "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-c:a", "aac", "-b:a", "160k",
                str(shorts_target),
            ]
            subprocess.run(fallback_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # 5. Extract Thumbnail at peak moment
        peak_offset = max(0.0, min(duration - 0.5, peak_time - clip_start))
        thumb_cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-ss", str(round(peak_offset, 2)),
            "-i", str(shorts_target if shorts_target.exists() else raw_clip_path),
            "-vframes", "1",
            "-q:v", "2",
            str(thumb_target),
        ]
        subprocess.run(thumb_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        return ViralClipRecord(
            clip_id=clip_uid,
            session_id=session_id,
            channel_name=channel_name,
            platform=platform,
            stream_url=stream_url,
            peak_time=peak_time,
            clip_start=clip_start,
            clip_end=clip_end,
            duration=duration,
            confidence_score=round(confidence_score, 1),
            demand_score=round(demand_score, 1),
            raw_clip_path=str(raw_clip_path),
            shorts_clip_path=str(shorts_target if shorts_target.exists() else raw_clip_path),
            thumbnail_path=str(thumb_target if thumb_target.exists() else ""),
            title=title,
            titles=titles,
            description=desc,
            tags=tags,
            transcript=transcript_text,
            status="PENDING_REVIEW",
            copyright_confirmed=False,
        )
