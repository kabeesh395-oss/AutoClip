from __future__ import annotations

import math
import time
from typing import Any

from .models import ChannelProfile, DemandScoreBreakdown

EXCITEMENT_TRANSCRIPT_KEYWORDS = {
    "insane", "unbelievable", "omg", "oh my god", "no way", "clutch", "holy",
    "what did he do", "did you see that", "what", "sheesh", "goat", "win", "winner",
    "dead", "kill", "record", "world record", "cheating", "banned", "lets go", "let's go"
}


class DemandEngine:
    """
    Dynamic Demand & Virality Scoring Engine.
    Combines multi-modal public signals: viewer counts, rolling growth rate,
    chat message velocity (MPS), unique chatter diversity, recent spike frequency,
    audio loudness surges, and transcript excitement triggers into a normalized 0-100 score.
    """

    def __init__(
        self,
        weight_viewers: float = 0.20,
        weight_growth: float = 0.15,
        weight_chat_mps: float = 0.25,
        weight_unique_chatters: float = 0.10,
        weight_spikes: float = 0.15,
        weight_audio: float = 0.10,
        weight_transcript: float = 0.05,
    ) -> None:
        self.w_viewers = weight_viewers
        self.w_growth = weight_growth
        self.w_chat_mps = weight_chat_mps
        self.w_unique_chatters = weight_unique_chatters
        self.w_spikes = weight_spikes
        self.w_audio = weight_audio
        self.w_transcript = weight_transcript

    def compute_demand_score(
        self,
        current_viewers: int = 0,
        viewer_history: list[tuple[float, int]] | None = None,
        chat_mps: float = 0.0,
        unique_chatters_ratio: float = 0.0,
        recent_spikes_count: int = 0,
        audio_excitement_score: float = 0.0,
        transcript_text: str = "",
        is_live: bool = True,
    ) -> DemandScoreBreakdown:
        """
        Calculates the aggregate demand score and detailed component breakdown.
        """
        if not is_live:
            # Baseline score for offline channels based on past viewers
            v_score = self._calc_viewer_score(current_viewers)
            total = round(v_score * 0.4, 1)
            return DemandScoreBreakdown(
                total_score=total,
                viewer_score=v_score,
                summary="Stream is currently OFFLINE; baseline historical interest only."
            )

        # 1. Viewer Score (0-100)
        s_viewers = self._calc_viewer_score(current_viewers)

        # 2. Viewer Growth Rate Score (0-100)
        growth_rate, s_growth = self._calc_growth_score(viewer_history, current_viewers)

        # 3. Chat MPS Score (0-100)
        s_mps = self._calc_mps_score(chat_mps)

        # 4. Unique Chatters Diversity Score (0-100)
        s_unique = min(100.0, max(0.0, unique_chatters_ratio * 100.0))

        # 5. Spike Frequency Score (0-100)
        # e.g., 1 spike in last 5m -> 50, 3+ spikes -> 90+
        s_spikes = min(100.0, max(0.0, recent_spikes_count * 30.0))

        # 6. Audio Excitement Score (0-100)
        s_audio = min(100.0, max(0.0, audio_excitement_score))

        # 7. Transcript Excitement Score (0-100)
        s_transcript = self._calc_transcript_score(transcript_text)

        # Weighted composite score
        composite = (
            self.w_viewers * s_viewers
            + self.w_growth * s_growth
            + self.w_chat_mps * s_mps
            + self.w_unique_chatters * s_unique
            + self.w_spikes * s_spikes
            + self.w_audio * s_audio
            + self.w_transcript * s_transcript
        )

        total_score = round(min(100.0, max(0.0, composite)), 1)

        summary_parts = []
        if current_viewers > 5000:
            summary_parts.append(f"High viewership ({current_viewers:,} concurrents)")
        if growth_rate > 5.0:
            summary_parts.append(f"Surging audience (+{growth_rate:.1f}%/min)")
        if chat_mps > 5.0:
            summary_parts.append(f"Rapid chat pace ({chat_mps:.1f} msgs/sec)")
        if recent_spikes_count > 0:
            summary_parts.append(f"{recent_spikes_count} hype spike(s) detected")
        if s_audio > 70.0:
            summary_parts.append("Elevated audio/shouting detected")

        summary = "; ".join(summary_parts) if summary_parts else "Steady live stream engagement"

        return DemandScoreBreakdown(
            total_score=total_score,
            viewer_score=round(s_viewers, 1),
            growth_score=round(s_growth, 1),
            chat_mps_score=round(s_mps, 1),
            unique_chatters_score=round(s_unique, 1),
            spike_frequency_score=round(s_spikes, 1),
            audio_excitement_score=round(s_audio, 1),
            transcript_excitement_score=round(s_transcript, 1),
            summary=summary,
        )

    def _calc_viewer_score(self, viewers: int) -> float:
        """
        Logarithmic normalization of viewer counts:
        10 viewers -> 25
        100 viewers -> 50
        1,000 viewers -> 70
        10,000 viewers -> 88
        50,000+ viewers -> 98-100
        """
        v = max(0, viewers)
        if v == 0:
            return 20.0
        score = 25.0 + (math.log10(v + 1) * 15.5)
        return min(100.0, max(0.0, score))

    def _calc_growth_score(
        self, viewer_history: list[tuple[float, int]] | None, current_viewers: int
    ) -> tuple[float, float]:
        """
        Computes rolling viewer growth rate (% change per minute) and maps to 0-100.
        """
        if not viewer_history or len(viewer_history) < 2:
            return 0.0, 50.0  # Neutral baseline

        # Compare current viewers with oldest entry in history within past 5-10 minutes
        oldest_t, oldest_v = viewer_history[0]
        now_t = time.time()
        elapsed_min = max(0.2, (now_t - oldest_t) / 60.0)

        if oldest_v <= 0:
            growth_rate = 0.0
        else:
            growth_rate = ((current_viewers - oldest_v) / oldest_v) / elapsed_min * 100.0

        # Positive growth scales above 50, negative drops below 50
        # +10% per min -> 75, +25% per min -> 95, -10% per min -> 30
        score = 50.0 + (growth_rate * 2.0)
        return growth_rate, min(100.0, max(10.0, score))

    def _calc_mps_score(self, mps: float) -> float:
        """
        Chat velocity score:
        0 MPS -> 10
        1 MPS -> 35
        5 MPS -> 65
        15 MPS -> 85
        30+ MPS -> 95-100
        """
        if mps <= 0:
            return 10.0
        score = 30.0 + (math.log10(mps + 1) * 45.0)
        return min(100.0, max(10.0, score))

    def _calc_transcript_score(self, text: str) -> float:
        if not text:
            return 0.0
        low = text.lower()
        hits = 0
        for phrase in EXCITEMENT_TRANSCRIPT_KEYWORDS:
            if " " in phrase and phrase in low:
                hits += 1
        words = low.split()
        hits += sum(1 for w in words if w.strip(".,!?;:\"'") in EXCITEMENT_TRANSCRIPT_KEYWORDS)
        # 1 hit -> 25, 3 hits -> 75, 4+ hits -> 100
        return min(100.0, hits * 25.0)
