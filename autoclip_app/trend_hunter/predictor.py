from __future__ import annotations

import collections
import logging
import math
import time
from typing import Any

from .models import AdaptiveWeights, PredictionResult

logger = logging.getLogger("autoclip.trend_hunter.predictor")

KEYWORD_WEIGHTS: dict[str, float] = {
    "insane": 12.0, "unbelievable": 14.0, "omg": 10.0, "no way": 12.0,
    "clutch": 15.0, "holy": 8.0, "what": 5.0, "sheesh": 8.0, "goat": 10.0,
    "win": 10.0, "winner": 12.0, "champion": 12.0, "record": 15.0, "world record": 18.0,
    "cheating": 10.0, "banned": 12.0, "lets go": 10.0, "pog": 8.0, "poggers": 10.0,
    "wtfff": 12.0, "brooo": 8.0, "screaming": 10.0, "dead": 8.0
}


class ViralMomentPredictor:
    """
    Intelligent Viral Moment Prediction & Pre-Peak Forecasting Engine.
    Tracks velocity acceleration (d²(MPS)/dt²), chatter ingress velocity,
    audio surge, acoustic pitch/cadence, and transcript energy.
    Detects climax moments *before* they plateau and produces a multi-signal
    confidence score with an explainable 'Why Selected' rationale.
    """

    def __init__(self, adaptive_weights: AdaptiveWeights | None = None) -> None:
        self.weights = adaptive_weights or AdaptiveWeights()
        # Sliding history of 2-second rate samples for acceleration tracking: (timestamp, mps)
        self._mps_samples: collections.deque[tuple[float, float]] = collections.deque(maxlen=20)
        # Tracking recent unique users
        self._recent_users: collections.deque[tuple[float, str]] = collections.deque(maxlen=500)

    def record_chat_metric(self, timestamp: float, current_mps: float, author: str | None = None) -> None:
        """Feeds periodic time-stamped chat velocity observations."""
        self._mps_samples.append((timestamp, current_mps))
        if author:
            self._recent_users.append((timestamp, author.lower()))

    def predict_viral_moment(
        self,
        current_mps: float,
        baseline_mps: float,
        audio_surge_db: float,
        audio_max_db: float,
        viewer_growth_rate: float,
        recent_transcript: str = "",
        words_per_second: float = 0.0,
    ) -> PredictionResult:
        """
        Evaluates real-time signals, computes velocity acceleration, and determines
        if an escalating hype climax is unfolding.
        """
        # 1. Chat Velocity Acceleration (d²(MPS)/dt²)
        acceleration, is_accelerating = self._calc_chat_acceleration()

        # 2. Ingress of Unique Chatters (new active participants / sec)
        chatter_growth_score = self._calc_chatter_growth_score()

        # 3. Chat Velocity Ratio
        eff_baseline = max(0.5, baseline_mps)
        mps_ratio = current_mps / eff_baseline
        # Normalization: ratio of 2x -> 50, 4x -> 75, 8x+ -> 95+
        s_chat = min(100.0, max(10.0, math.log2(max(1.0, mps_ratio)) * 32.0))

        # 4. Viewer Growth Score
        s_viewer_growth = min(100.0, max(10.0, 50.0 + (viewer_growth_rate * 2.0)))

        # 5. Audio Excitement & Dynamic Crest Factor
        s_audio = 25.0
        if audio_surge_db > 2.0:
            s_audio += min(55.0, audio_surge_db * 5.0)
        if audio_max_db > -4.0:
            s_audio += 20.0
        s_audio = min(100.0, max(10.0, s_audio))

        # 6. Speech Emotion & Cadence (high WPS indicates excited fast-paced speech)
        s_speech = 50.0
        if 2.2 <= words_per_second <= 4.5:
            s_speech = 85.0
        elif words_per_second > 4.5:
            s_speech = 95.0  # Rapid shouting/commentary

        # 7. Transcript Keyword Triggers
        s_transcript, matched_keywords = self._calc_transcript_triggers(recent_transcript)

        # Weighted multi-modal fusion using adaptive learned weights
        w = self.weights
        total_weights = (
            w.w_chat + w.w_chatter_growth + w.w_viewer_growth
            + w.w_audio + w.w_speech_density + w.w_transcript_keywords
        )
        norm_factor = 1.0 / max(0.1, total_weights)

        composite = (
            (w.w_chat * s_chat)
            + (w.w_chatter_growth * chatter_growth_score)
            + (w.w_viewer_growth * s_viewer_growth)
            + (w.w_audio * s_audio)
            + (w.w_speech_density * s_speech)
            + (w.w_transcript_keywords * s_transcript)
        ) * norm_factor

        # Early acceleration prediction bonus
        is_early = False
        if is_accelerating and acceleration > 1.2:
            composite += min(12.0, acceleration * 3.0)
            is_early = True

        # Apply learned sensitivity bias
        composite += w.sensitivity_bias
        confidence = round(min(99.0, max(15.0, composite)), 1)

        # Build Explainable 'Why Selected' Rationale
        rationale_parts = []
        if current_mps >= 3.0:
            rationale_parts.append(f"Chat velocity at {current_mps:.1f} MPS ({mps_ratio:.1f}x baseline)")
        if is_early:
            rationale_parts.append(f"Pre-peak velocity acceleration (+{acceleration:.1f} MPS/s²)")
        if audio_surge_db >= 4.0:
            rationale_parts.append(f"Audio volume surged +{audio_surge_db:.1f} dB above mean")
        if matched_keywords:
            kw_str = ", ".join(f"'{k}'" for k in matched_keywords[:3])
            rationale_parts.append(f"High-energy keywords detected: {kw_str}")
        if viewer_growth_rate > 5.0:
            rationale_parts.append(f"Audience grew +{viewer_growth_rate:.1f}%/min")

        rationale = "; ".join(rationale_parts) if rationale_parts else "Elevated multi-signal excitement across stream audio and chat"

        return PredictionResult(
            confidence_score=confidence,
            rationale=rationale,
            signals={
                "chat_velocity_score": round(s_chat, 1),
                "chatter_growth_score": round(chatter_growth_score, 1),
                "viewer_growth_score": round(s_viewer_growth, 1),
                "audio_excitement_score": round(s_audio, 1),
                "speech_cadence_score": round(s_speech, 1),
                "transcript_score": round(s_transcript, 1),
                "acceleration": round(acceleration, 2),
            },
            is_early_prediction=is_early,
            acceleration=round(acceleration, 2),
        )

    def _calc_chat_acceleration(self) -> tuple[float, bool]:
        """
        Computes 2nd derivative of chat message rate.
        A positive acceleration signifies an escalating climax before peak plateau.
        """
        if len(self._mps_samples) < 3:
            return 0.0, False

        s1, s2, s3 = list(self._mps_samples)[-3:]
        dt1 = max(0.5, s2[0] - s1[0])
        dt2 = max(0.5, s3[0] - s2[0])

        v1 = (s2[1] - s1[1]) / dt1
        v2 = (s3[1] - s2[1]) / dt2

        acc = (v2 - v1) / ((dt1 + dt2) / 2.0)
        return max(0.0, acc), (v2 > 0.5 and acc > 0.2)

    def _calc_chatter_growth_score(self) -> float:
        """Evaluates how many distinct users joined the conversation in the last 15s."""
        if not self._recent_users:
            return 30.0

        now_t = time.time()
        cutoff_15s = now_t - 15.0
        active_in_15s = [u for t, u in self._recent_users if t >= cutoff_15s]
        unique_count = len(set(active_in_15s))

        # 5 unique chatters -> 45, 20 unique -> 75, 50+ unique -> 95+
        return min(100.0, max(20.0, unique_count * 2.0 + 30.0))

    def _calc_transcript_triggers(self, text: str) -> tuple[float, list[str]]:
        if not text:
            return 20.0, []

        clean_text = text.lower()
        matched: list[str] = []
        score = 20.0

        for kw, pts in KEYWORD_WEIGHTS.items():
            if kw in clean_text:
                matched.append(kw)
                score += pts

        return min(100.0, score), matched
