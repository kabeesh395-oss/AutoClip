from __future__ import annotations

import collections
import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger("autoclip.trend_hunter.audio")


class AudioLoudnessMonitor:
    """
    Analyzes live audio chunks using FFmpeg volumedetect/ebur128.
    Maintains a rolling baseline of stream audio loudness, detects sudden
    shouting/cheering energy surges, and filters out false-positives caused
    by steady loud background music.
    """

    def __init__(self, baseline_history_size: int = 30) -> None:
        # History of mean volume measurements (in dB)
        self._mean_volume_history: collections.deque[float] = collections.deque(maxlen=baseline_history_size)
        self._max_volume_history: collections.deque[float] = collections.deque(maxlen=baseline_history_size)
        self.latest_mean_db: float = -40.0
        self.latest_max_db: float = -30.0

    def analyze_audio_segment(self, file_path: Path) -> tuple[float, float, float]:
        """
        Runs FFmpeg volumedetect on the given media file or chunk.
        Returns: (mean_volume_db, max_volume_db, excitement_score_0_to_100)
        """
        if not file_path.exists() or file_path.stat().st_size == 0:
            return -60.0, -60.0, 0.0

        cmd = [
            "ffmpeg", "-hide_banner",
            "-i", str(file_path),
            "-vn",
            "-af", "volumedetect",
            "-f", "null", "-",
        ]

        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=12.0)
            output = res.stderr
        except Exception as exc:
            logger.debug("Audio analysis timed out or failed for %s: %s", file_path, exc)
            return self.latest_mean_db, self.latest_max_db, 0.0

        mean_match = re.search(r"mean_volume:\s*(-?[0-9.]+)\s*dB", output)
        max_match = re.search(r"max_volume:\s*(-?[0-9.]+)\s*dB", output)

        mean_db = float(mean_match.group(1)) if mean_match else -40.0
        max_db = float(max_match.group(1)) if max_match else -20.0

        self.latest_mean_db = mean_db
        self.latest_max_db = max_db

        # Compute excitement score comparing with baseline
        excitement_score = self._compute_excitement_score(mean_db, max_db)

        # Update history
        self._mean_volume_history.append(mean_db)
        self._max_volume_history.append(max_db)

        return mean_db, max_db, excitement_score

    def _compute_excitement_score(self, current_mean: float, current_max: float) -> float:
        """
        Evaluates energy surge above baseline while filtering sustained music loudness.
        """
        if not self._mean_volume_history:
            # First observation: score solely on absolute peaks
            if current_max > -3.0:
                return 75.0
            if current_max > -8.0:
                return 50.0
            return 25.0

        baseline_mean = sum(self._mean_volume_history) / len(self._mean_volume_history)
        surge_db = current_mean - baseline_mean
        crest_factor = current_max - current_mean

        # Shouting or cheering typically exhibits:
        # 1. Surge >= 5-10 dB above rolling mean
        # 2. High crest factor (punchy transients: > 12-15 dB between mean and peak)
        # Background music exhibits low crest factor (< 8 dB) and no sudden surge
        score = 30.0

        if surge_db > 3.0:
            score += surge_db * 4.5  # +10 dB surge -> +45 points

        if current_max > -5.0:
            score += 20.0
        elif current_max > -10.0:
            score += 10.0

        # Crest factor bonus for shouting/screaming
        if crest_factor > 14.0:
            score += 15.0
        elif crest_factor < 6.0:
            # Steady compressed audio (music/monotone) -> dampen score
            score -= 15.0

        return round(min(100.0, max(0.0, score)), 1)
