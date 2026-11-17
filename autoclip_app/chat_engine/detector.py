from __future__ import annotations

import collections
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable

from .models import ChatMessage, ChatWindowMetrics, SpikeDetectorConfig, SpikeEvent

logger = logging.getLogger("autoclip.chat.detector")

STOPWORDS = {
    "the", "a", "an", "is", "it", "to", "in", "and", "or", "of", "for", "on", "at",
    "by", "this", "that", "you", "i", "he", "she", "we", "they", "me", "my", "your"
}


def compute_shannon_entropy(words: list[str]) -> float:
    if not words:
        return 0.0
    counts = collections.Counter(words)
    total = len(words)
    entropy = 0.0
    for cnt in counts.values():
        p = cnt / total
        entropy -= p * math.log2(p)
    # Normalize by max possible entropy log2(unique_tokens or len)
    max_h = math.log2(max(len(counts), 2))
    return min(1.0, max(0.0, entropy / max_h)) if max_h > 0 else 0.0


class ChatSpikeDetector:
    """
    Real-time chat monitoring and statistical spike detection engine.
    Maintains sliding time-window queues, tracks rolling baseline activity,
    filters spam/bots, and coalesces highlight events into candidate clips.
    """

    def __init__(
        self,
        config: SpikeDetectorConfig | None = None,
        session_id: str = "default_session",
        on_spike_callback: Callable[[SpikeEvent], Any] | None = None,
    ) -> None:
        self.config = config or SpikeDetectorConfig()
        self.session_id = session_id
        self.on_spike_callback = on_spike_callback

        # Sliding window buffer: stores (timestamp, ChatMessage)
        self._message_buffer: collections.deque[ChatMessage] = collections.deque()

        # Spam protection: sliding window of messages per user
        # user -> deque of timestamps
        self._user_timestamps: dict[str, collections.deque[float]] = collections.defaultdict(collections.deque)

        # Baseline history: list of past 10-second MPS rates over baseline window
        self._historical_10s_rates: collections.deque[float] = collections.deque(maxlen=60)
        self._last_baseline_sample_t: float = 0.0

        # Active or recent spikes to prevent duplicates and enable coalescing
        self.detected_spikes: list[SpikeEvent] = []
        self._active_spike: SpikeEvent | None = None

        # Statistics
        self.total_messages_received = 0
        self.total_messages_filtered = 0

    def feed_message(self, msg: ChatMessage) -> SpikeEvent | None:
        """
        Feed an incoming chat message into the pipeline.
        Returns a new SpikeEvent if a new spike is confirmed or coalesced.
        """
        self.total_messages_received += 1

        # 1. Ignore known bots
        if msg.is_bot:
            self.total_messages_filtered += 1
            return None

        # 2. Per-user rate limiting (anti-spam)
        user_dq = self._user_timestamps[msg.author.lower()]
        # Evict timestamps older than 5.0 seconds
        cutoff_5s = msg.timestamp - 5.0
        while user_dq and user_dq[0] < cutoff_5s:
            user_dq.popleft()

        if len(user_dq) >= self.config.spam_rate_limit:
            # Drop spam messages exceeding rate limit
            self.total_messages_filtered += 1
            return None

        user_dq.append(msg.timestamp)
        self._message_buffer.append(msg)

        # 3. Evict messages older than baseline window + 60s
        max_retention = self.config.baseline_window_seconds + 60.0
        cutoff_buffer = msg.timestamp - max_retention
        while self._message_buffer and self._message_buffer[0].timestamp < cutoff_buffer:
            self._message_buffer.popleft()

        # 4. Periodically update baseline rates (every 5 seconds)
        if (msg.timestamp - self._last_baseline_sample_t) >= 5.0:
            self._update_baseline_samples(msg.timestamp)
            self._last_baseline_sample_t = msg.timestamp

        # 5. Evaluate current 10-second window for spike
        return self._evaluate_spike(msg.timestamp)

    def _update_baseline_samples(self, current_time: float) -> None:
        """Calculates 10s rate over past intervals to maintain baseline statistics."""
        m_10s = self.get_window_metrics(current_time, 10.0)
        self._historical_10s_rates.append(m_10s.mps)

    def get_baseline_stats(self) -> tuple[float, float]:
        """Returns (baseline_mean_mps, baseline_std_mps)."""
        if not self._historical_10s_rates:
            return 0.5, 0.5
        rates = list(self._historical_10s_rates)
        mean_mps = sum(rates) / len(rates)
        if len(rates) < 2:
            std_mps = 0.5
        else:
            variance = sum((r - mean_mps) ** 2 for r in rates) / (len(rates) - 1)
            std_mps = math.sqrt(variance)
        return max(mean_mps, 0.3), max(std_mps, 0.5)

    def get_window_metrics(self, current_time: float, window_seconds: float) -> ChatWindowMetrics:
        """Computes statistical activity metrics for a specific time window [t - W, t]."""
        start_t = current_time - window_seconds
        # Collect messages in range
        msgs = [m for m in self._message_buffer if start_t <= m.timestamp <= current_time]
        count = len(msgs)
        mps = count / max(window_seconds, 1.0)

        if not msgs:
            return ChatWindowMetrics(
                window_seconds=window_seconds,
                message_count=0,
                mps=0.0,
                unique_users=0,
                unique_user_ratio=0.0,
                entropy=0.0,
                emote_density=0.0,
                caps_ratio=0.0,
            )

        unique_users = len({m.author.lower() for m in msgs})
        user_ratio = unique_users / max(count, 1)

        # Emote count & caps ratio
        total_emotes = sum(len(m.emotes) for m in msgs)
        avg_caps = sum(m.caps_ratio for m in msgs) / count
        emote_density = total_emotes / max(count, 1)

        # Lexical words for entropy
        words: list[str] = []
        for m in msgs:
            tokens = re.findall(r"\w+", m.text.lower())
            words.extend([w for w in tokens if w not in STOPWORDS])
        entropy = compute_shannon_entropy(words)

        return ChatWindowMetrics(
            window_seconds=window_seconds,
            message_count=count,
            mps=mps,
            unique_users=unique_users,
            unique_user_ratio=user_ratio,
            entropy=entropy,
            emote_density=emote_density,
            caps_ratio=avg_caps,
        )

    def _evaluate_spike(self, current_time: float) -> SpikeEvent | None:
        """Evaluates whether current chat velocity qualifies as a genuine highlight spike."""
        m5 = self.get_window_metrics(current_time, 5.0)
        m10 = self.get_window_metrics(current_time, 10.0)
        baseline_mean, baseline_std = self.get_baseline_stats()

        # Floor check: must meet absolute minimum MPS
        if m10.mps < self.config.min_mps_threshold and m5.mps < self.config.min_mps_threshold:
            return None

        # Z-score on 10s rate
        z_score = (m10.mps - baseline_mean) / baseline_std
        burst_ratio = m10.mps / baseline_mean

        # Threshold evaluation
        is_surge = (z_score >= self.config.z_threshold) or (burst_ratio >= self.config.burst_multiplier)
        if not is_surge:
            # Also check fast 5s burst (e.g. sudden 5x spike in 5s)
            fast_ratio = m5.mps / baseline_mean
            if fast_ratio < (self.config.burst_multiplier * 1.3):
                return None

        # Anti-spam filter: reject if driven by fewer than 2 unique users
        if m10.unique_users < 2 or m10.unique_user_ratio < 0.20:
            return None

        # Calculate multi-signal confidence score (0-100)
        confidence = self._calculate_confidence(m10, baseline_mean, z_score)
        if confidence < self.config.min_confidence:
            return None

        # Calculate clip boundaries
        pre_roll = self.config.pre_roll_seconds
        post_roll = self.config.post_roll_seconds
        clip_start = max(0.0, current_time - pre_roll)
        clip_end = current_time + post_roll

        # Extract top emotes and keywords in this hype window
        dominant_emotes, dominant_keywords = self._extract_dominant_signals(current_time, 15.0)

        # Check for Coalescing: If within coalesce_window of active/last spike, merge!
        if self._active_spike:
            time_since_last_peak = current_time - self._active_spike.peak_time
            if time_since_last_peak <= self.config.coalesce_window_seconds:
                # Merge into existing spike
                self._active_spike.end_time = max(self._active_spike.end_time, current_time)
                self._active_spike.clip_end = max(self._active_spike.clip_end, clip_end)
                if m10.mps > self._active_spike.peak_mps:
                    self._active_spike.peak_mps = m10.mps
                    self._active_spike.peak_time = current_time
                    self._active_spike.z_score = max(self._active_spike.z_score, z_score)
                self._active_spike.confidence_score = max(self._active_spike.confidence_score, confidence)
                # Combine unique emotes & keywords
                combined_emotes = list(dict.fromkeys(self._active_spike.dominant_emotes + dominant_emotes))[:5]
                combined_keywords = list(dict.fromkeys(self._active_spike.dominant_keywords + dominant_keywords))[:5]
                self._active_spike.dominant_emotes = combined_emotes
                self._active_spike.dominant_keywords = combined_keywords
                self._active_spike.title = self._generate_title(self._active_spike)
                return self._active_spike

        # Create new SpikeEvent
        spike = SpikeEvent(
            session_id=self.session_id,
            start_time=max(0.0, current_time - 10.0),
            peak_time=current_time,
            end_time=current_time,
            baseline_mps=baseline_mean,
            peak_mps=m10.mps,
            z_score=z_score,
            confidence_score=confidence,
            dominant_emotes=dominant_emotes,
            dominant_keywords=dominant_keywords,
            clip_start=clip_start,
            clip_end=clip_end,
            status="DETECTED",
        )
        spike.title = self._generate_title(spike)
        spike.description = self._generate_description(spike)

        self._active_spike = spike
        self.detected_spikes.append(spike)

        if self.on_spike_callback:
            try:
                self.on_spike_callback(spike)
            except Exception as e:
                logger.error("Error in on_spike_callback: %s", e)

        return spike

    def _calculate_confidence(self, m: ChatWindowMetrics, baseline_mean: float, z_score: float) -> float:
        """
        Combines message frequency, viewer diversity, lexical entropy, and reaction bursts
        into a normalized confidence score (0 to 100).
        """
        # 1. Frequency Score (0 to 40 pts)
        rel_boost = m.mps / max(baseline_mean, 0.5)
        freq_score = min(40.0, (rel_boost / 4.0) * 40.0)

        # 2. Viewer Diversity Score (0 to 20 pts)
        # Ratio of unique users to messages
        user_score = min(20.0, m.unique_user_ratio * 20.0)

        # 3. Lexical Entropy Score (0 to 15 pts)
        # Prevents bot copypasta attacks
        entropy_score = min(15.0, m.entropy * 15.0)

        # 4. Reaction / Emote Burst Score (0 to 15 pts)
        # High emote density or high caps excitement
        emote_score = min(10.0, m.emote_density * 5.0)
        caps_score = min(5.0, m.caps_ratio * 5.0)

        # 5. Z-Score Statistical Bonus (0 to 10 pts)
        z_bonus = min(10.0, max(0.0, (z_score - 2.0) * 3.3))

        total = freq_score + user_score + entropy_score + emote_score + caps_score + z_bonus
        return min(100.0, max(0.0, total))

    def _extract_dominant_signals(self, current_time: float, window_sec: float) -> tuple[list[str], list[str]]:
        """Identifies top emotes and keywords around the spike peak."""
        start_t = current_time - window_sec
        msgs = [m for m in self._message_buffer if start_t <= m.timestamp <= current_time]

        emotes_list: list[str] = []
        words_list: list[str] = []
        for m in msgs:
            emotes_list.extend(m.emotes)
            tokens = re.findall(r"[a-zA-Z]{3,}", m.text.lower())
            words_list.extend([w for w in tokens if w not in STOPWORDS and w not in m.emotes])

        top_emotes = [e.upper() for e, _ in collections.Counter(emotes_list).most_common(4)]
        top_words = [w.capitalize() for w, _ in collections.Counter(words_list).most_common(4)]
        return top_emotes, top_words

    def _generate_title(self, spike: SpikeEvent) -> str:
        """Generates a viral title based on peak emotes and keywords."""
        emotes_str = " ".join(spike.dominant_emotes[:3]) if spike.dominant_emotes else "🔥"
        keywords_str = " ".join(spike.dominant_keywords[:2]) if spike.dominant_keywords else "Hype Moment"

        if "W" in spike.dominant_emotes or "CLUTCH" in [k.upper() for k in spike.dominant_keywords]:
            return f"INSANE CLUTCH PLAY! Chat Goes Wild {emotes_str}"
        if "POG" in spike.dominant_emotes or "POGGERS" in spike.dominant_emotes:
            return f"POG CHAMPION! Unbelievable Reaction {emotes_str}"
        if "LOL" in spike.dominant_emotes or "OMEGALUL" in spike.dominant_emotes or "KEKW" in spike.dominant_emotes:
            return f"CHAT CANNOT STOP LAUGHING! Funniest Stream Moment {emotes_str}"

        return f"CHAT SPIKE DETECTED: {keywords_str} {emotes_str}"

    def _generate_description(self, spike: SpikeEvent) -> str:
        emotes = ", ".join(spike.dominant_emotes) if spike.dominant_emotes else "None"
        return (
            f"Automated AutoClip AI highlight generated via chat spike detection.\n"
            f"Peak Activity: {round(spike.peak_mps, 1)} msgs/sec (Baseline: {round(spike.baseline_mps, 1)} msgs/sec)\n"
            f"Confidence Score: {round(spike.confidence_score, 1)}/100 | Statistical Z-Score: {round(spike.z_score, 2)}\n"
            f"Dominant Emotes: {emotes}\n"
            f"Clip Interval: {round(spike.clip_start, 1)}s to {round(spike.clip_end, 1)}s (Duration: {round(spike.duration, 1)}s)"
        )
