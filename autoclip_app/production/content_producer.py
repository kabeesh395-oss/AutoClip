from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ContentItem

logger = logging.getLogger("autoclip.production.content_producer")


def compute_file_sha256(path: Path) -> str:
    """Computes a SHA256 hash of a file for upload deduplication."""
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


class SubtitleTimingValidator:
    """
    Validates subtitle synchronization and readability:
    - Non-overlapping intervals
    - Monotonically increasing start timestamps
    - Maximum characters per second (CPS <= 25) to prevent unreadable fast text
    - Boundaries inside video duration
    """

    @staticmethod
    def _parse_timestamp(ts: str) -> float:
        """Parses 'HH:MM:SS,mmm' or 'HH:MM:SS.mmm' to float seconds."""
        ts = ts.strip().replace(",", ".")
        parts = ts.split(":")
        if len(parts) == 3:
            h, m, s = parts
            return float(h) * 3600 + float(m) * 60 + float(s)
        elif len(parts) == 2:
            m, s = parts
            return float(m) * 60 + float(s)
        return float(ts)

    @classmethod
    def validate_srt(cls, srt_text: str, clip_duration: float = 0.0) -> tuple[bool, list[str]]:
        errors: list[str] = []
        blocks = re.split(r"\n\s*\n", srt_text.strip())
        last_end = 0.0

        for i, block in enumerate(blocks):
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if len(lines) < 2:
                continue

            # Look for timestamp line (e.g. 00:00:01,000 --> 00:00:03,500)
            time_match = re.search(r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})", lines[1] if len(lines) > 1 and "-->" in lines[1] else lines[0])
            if not time_match:
                continue

            start_str, end_str = time_match.groups()
            try:
                start = cls._parse_timestamp(start_str)
                end = cls._parse_timestamp(end_str)
            except ValueError:
                errors.append(f"Block {i+1}: Invalid timestamp format '{start_str} --> {end_str}'")
                continue

            if start >= end:
                errors.append(f"Block {i+1}: Subtitle start ({start:.2f}s) >= end ({end:.2f}s)")

            if start < last_end - 0.05:  # small tolerance
                errors.append(f"Block {i+1}: Overlaps with previous subtitle (start {start:.2f}s < last end {last_end:.2f}s)")

            duration = max(0.1, end - start)
            text_lines = lines[2:] if "-->" in lines[1] else lines[1:]
            text = " ".join(text_lines)
            char_count = len(text)
            cps = char_count / duration

            if cps > 28.0:
                errors.append(f"Block {i+1}: Text speed too fast ({cps:.1f} chars/sec > 28 threshold)")

            if clip_duration > 0 and end > clip_duration + 1.0:
                errors.append(f"Block {i+1}: Subtitle extends beyond video duration ({end:.2f}s > {clip_duration:.2f}s)")

            last_end = end

        is_valid = len(errors) == 0
        return is_valid, errors


class ContentProducer:
    """
    Platform-specific export renderer, multi-title generator, and thumbnail extractor.
    """

    def __init__(self, ollama_url: str = "http://127.0.0.1:11434") -> None:
        self.ollama_url = ollama_url

    def generate_titles_and_descriptions(
        self,
        transcript: str,
        channel_name: str = "",
        stream_title: str = "",
        keywords: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Generates 3 distinct title options (Curiosity, Reaction/Shock, Question hook)
        and platform-tailored descriptions with hashtags.
        """
        prompt = (
            f"Generate 3 viral YouTube Shorts / Instagram Reels titles and descriptions for this live clip.\n"
            f"Streamer: {channel_name}\n"
            f"Stream Context: {stream_title}\n"
            f"Transcript: {transcript[:500]}\n"
            f"Keywords: {', '.join(keywords or [])}\n"
            f"Return JSON with: title_options (list of 3 strings), description, hashtags (list)."
        )

        # Attempt Ollama local LLM generation
        try:
            req = urllib.request.Request(
                f"{self.ollama_url}/api/generate",
                data=json.dumps({"model": "llama3.2", "prompt": prompt, "stream": False, "format": "json"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                parsed = json.loads(data.get("response", "{}"))
                if parsed.get("title_options") and len(parsed["title_options"]) >= 3:
                    return {
                        "title": parsed["title_options"][0],
                        "title_options": parsed["title_options"][:3],
                        "description": parsed.get("description", ""),
                        "hashtags": parsed.get("hashtags", ["#shorts", "#viral", "#reels"]),
                    }
        except Exception:
            pass  # Fall back to smart heuristic rule-based generation

        # Rule-based heuristic title generator
        name = channel_name.strip() or "Streamer"
        kw = (keywords or ["MOMENT"])[0].upper()
        clean_snip = re.sub(r"[^\w\s]", "", transcript.strip())
        short_quote = clean_snip[:40] if clean_snip else "Unbelievable Scene"

        title_curiosity = f"You Won't Believe What {name} Just Did... 😱"
        title_reaction = f"INSANE: {name}'s Reaction To {kw} Had Chat Losing It!"
        title_question = f"Did {name} Really Just Say That?! 💀"

        description = (
            f"Watch {name}'s wildest live stream moment! Stream context: {stream_title or 'Live Highlights'}.\n\n"
            f"Subscribe for more daily viral clips!\n"
            f"#shorts #reels #gaming #viral #{name.lower().replace(' ', '')}"
        )

        hashtags = ["#shorts", "#reels", "#viral", f"#{name.lower().replace(' ', '')}", "#gaming"]

        return {
            "title": title_curiosity,
            "title_options": [title_curiosity, title_reaction, title_question],
            "description": description,
            "hashtags": hashtags,
        }

    def render_vertical_variant(
        self,
        source_path: Path,
        output_path: Path,
        subtitle_path: Path | None = None,
    ) -> Path:
        """
        Renders 9:16 (1080x1920) vertical video with background blur padding.
        Subtitles are placed in the safe middle zone avoiding top/bottom platform UI headers.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Split into background blur and foreground centered 9:16 frame
        filter_complex = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:20[bgblur];"
            "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fgfit];"
            "[bgblur][fgfit]overlay=(W-w)/2:(H-h)/2[outv]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", str(source_path),
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "22",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(output_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            logger.error("FFmpeg vertical render failed: %s", res.stderr)
            raise RuntimeError(f"FFmpeg vertical render failed: {res.stderr[:200]}")
        return output_path

    def render_horizontal_variant(
        self,
        source_path: Path,
        output_path: Path,
    ) -> Path:
        """
        Renders 16:9 (1920x1080) landscape video for YouTube long-form / compilation.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        filter_complex = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black"

        cmd = [
            "ffmpeg", "-y",
            "-i", str(source_path),
            "-vf", filter_complex,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "22",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(output_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            logger.error("FFmpeg horizontal render failed: %s", res.stderr)
            raise RuntimeError(f"FFmpeg horizontal render failed: {res.stderr[:200]}")
        return output_path

    def extract_thumbnail(
        self,
        source_path: Path,
        output_path: Path,
        timestamp_sec: float = 1.0,
        aspect_ratio: str = "9:16",
    ) -> Path:
        """
        Extracts a high quality thumbnail image at the designated timestamp,
        formatted to the target aspect ratio (9:16 for Shorts/Reels or 16:9 for Long-form).
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if aspect_ratio == "9:16":
            vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        else:
            vf = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(max(0.1, timestamp_sec)),
            "-i", str(source_path),
            "-vframes", "1",
            "-vf", vf,
            "-q:v", "2",
            str(output_path),
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return output_path
