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
from .advanced_editing import AdvancedClipEditor
from .models import TrendHunterConfig, ViralClipRecord

logger = logging.getLogger("autoclip.trend_hunter.clip_pipeline")

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS_DIR = ROOT / "data" / "outputs" / "viral_clips"


class ViralClipPipeline:
    """
    Production-grade AI Clip Pipeline for Phase 2:
      - Dead-air & silence trimming
      - Faster-Whisper word-level transcription
      - Kinetic electric yellow karaoke ASS subtitles
      - Dynamic reframing (FIT_BLUR, SPLIT_STACK, SMART_CROP)
      - Quality Assurance & Validation check
      - AI Hook, 5 viral titles, description, and hashtags
    """

    def __init__(self, output_dir: Path | None = None) -> None:
        self.output_dir = output_dir or OUTPUTS_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.editor = AdvancedClipEditor(output_dir=self.output_dir)

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
        why_selected: str = "",
        config: TrendHunterConfig | None = None,
    ) -> ViralClipRecord:
        """
        Executes end-to-end editing, formatting, and quality check on raw video slice.
        """
        cfg = config or TrendHunterConfig()
        clip_uid = f"viral_{uuid.uuid4().hex[:8]}"
        duration = max(1.0, clip_end - clip_start)

        # File paths
        trimmed_raw = self.output_dir / f"{clip_uid}_trimmed.mp4"
        shorts_target = self.output_dir / f"{clip_uid}_shorts.mp4"
        thumb_target = self.output_dir / f"{clip_uid}_thumb.jpg"
        ass_target = self.output_dir / f"{clip_uid}.ass"

        # 1. Dead-Air & Silence Removal (if enabled)
        effective_source = raw_clip_path
        if cfg.remove_silence:
            try:
                effective_source = self.editor.remove_silence(
                    input_path=raw_clip_path,
                    output_path=trimmed_raw,
                    threshold_db=cfg.silence_threshold_db,
                    min_silence_sec=cfg.silence_duration_sec,
                )
            except Exception as exc:
                logger.debug("Silence trimming step skipped: %s", exc)
                effective_source = raw_clip_path

        # 2. Faster-Whisper Speech Transcription
        segments: list[Segment] = []
        transcript_text = ""
        try:
            whisper_model = os.getenv("WHISPER_MODEL", "small")
            segments = transcribe(effective_source, model_name=whisper_model)
            transcript_text = " ".join(s.text for s in segments).strip()
        except Exception as exc:
            logger.warning("Whisper transcription failed or no speech detected (%s)", exc)
            transcript_text = f"High-energy moment from {channel_name}'s live stream!"

        # 3. Viral Titles, Hooks, and Descriptions
        cand = Candidate(
            start=0.0,
            end=duration,
            title=f"{channel_name} Viral Moment",
            reason=why_selected or f"Moment detected with {confidence_score:.0f}% confidence",
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

        # 4. Kinetic ASS Subtitles with Visual Hook Banner
        hook_banner_text = intel.get("hook", {}).get("text") or title
        ass_path = None
        if segments:
            try:
                generate_opus_ass(
                    segments,
                    clip_start=0.0,
                    clip_end=duration,
                    target_ass=ass_target,
                    caption_style=cfg.caption_style,
                    hook_title=hook_banner_text,
                )
                ass_path = ass_target
            except Exception as exc:
                logger.warning("Failed to generate kinetic ASS subtitles: %s", exc)

        # 5. Intelligent Reframing (FIT_BLUR, SPLIT_STACK, SMART_CROP)
        self.editor.reframe_to_vertical(
            input_path=effective_source,
            output_path=shorts_target,
            layout=cfg.layout,
            ass_path=ass_path,
        )

        # 6. Extract Thumbnail at peak moment
        peak_offset = max(0.0, min(duration - 0.5, peak_time - clip_start))
        thumb_cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-ss", str(round(peak_offset, 2)),
            "-i", str(shorts_target if shorts_target.exists() else effective_source),
            "-vframes", "1",
            "-q:v", "2",
            str(thumb_target),
        ]
        subprocess.run(thumb_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        # 7. Automated Quality Assurance Check
        final_video = shorts_target if shorts_target.exists() else effective_source
        qc_passed, qc_issues = self.editor.run_quality_check(final_video, transcript_text)

        # Determine initial status based on automation level
        initial_status = "PENDING_REVIEW"
        if cfg.automation_level == "SEMI_AUTO" and qc_passed and confidence_score >= 80.0:
            initial_status = "PRE_APPROVED"

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
            shorts_clip_path=str(final_video),
            thumbnail_path=str(thumb_target if thumb_target.exists() else ""),
            title=title,
            titles=titles,
            description=desc,
            tags=tags,
            transcript=transcript_text,
            why_selected=why_selected,
            status=initial_status,
            quality_check_passed=qc_passed,
            quality_issues=qc_issues,
            copyright_confirmed=False,
        )
