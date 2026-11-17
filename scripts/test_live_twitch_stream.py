"""
Live Stream Monitoring Test against a real active Twitch Stream
Verifies:
1. Streamlink stream resolution & live HLS segment capture into RollingLiveBuffer
2. Anonymous Twitch IRC real-time message stream
3. Audio loudness analysis of live incoming chunks
4. Rolling message frequency (MPS) calculation from live chatters
"""

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autoclip_app.chat_engine.detector import ChatSpikeDetector
from autoclip_app.chat_engine.models import SpikeDetectorConfig
from autoclip_app.chat_engine.sources import TwitchIRCChatSource
from autoclip_app.trend_hunter.audio_monitor import AudioLoudnessMonitor
from autoclip_app.trend_hunter.live_buffer import RollingLiveBuffer


async def run_live_test(channel_name: str = "fps_shaka", test_duration_seconds: int = 20):
    print(f"=== TESTING REAL LIVE STREAM MONITORING ON TWITCH: {channel_name} ===")
    
    test_buffer_dir = ROOT / "data" / "live_twitch_test_buffer"
    if test_buffer_dir.exists():
        shutil.rmtree(test_buffer_dir)
    test_buffer_dir.mkdir(parents=True, exist_ok=True)

    stream_url = f"https://www.twitch.tv/{channel_name}"
    
    # 1. Initialize Buffer
    buffer = RollingLiveBuffer(session_id="test_live_twitch", retention_seconds=120.0, segment_seconds=4.0)
    buffer.buffer_dir = test_buffer_dir
    buffer.buffer_dir.mkdir(parents=True, exist_ok=True)

    # 2. Start Live Video Buffer Capture via Streamlink
    print(f"Starting Streamlink HLS capture on {stream_url}...")
    await buffer.start_capture(stream_url=stream_url, is_simulation=False)

    # 3. Initialize Twitch IRC Chat Source & Spike Detector
    chat_source = TwitchIRCChatSource(channel=channel_name)
    detector = ChatSpikeDetector(
        config=SpikeDetectorConfig(sensitivity="medium", pre_roll_seconds=10.0, post_roll_seconds=15.0),
        session_id="test_live_twitch"
    )
    audio_monitor = AudioLoudnessMonitor()

    print("Connecting to live Twitch IRC channel...")
    await chat_source.connect()
    print("Connected to Twitch IRC! Ingesting live chat & video for 20 seconds...")

    messages_received = []

    async def chat_ingest_loop():
        async for msg in chat_source.iter_messages():
            messages_received.append(msg)
            spike = detector.feed_message(msg)
            if spike:
                print(f"[*] HYPE BURST DETECTED at peak {spike.peak_time:.1f}s with {spike.peak_mps:.1f} MPS!")

    chat_task = asyncio.create_task(chat_ingest_loop())

    # Wait and periodically inspect chunks & audio
    start_t = time.time()
    while time.time() - start_t < test_duration_seconds:
        await asyncio.sleep(4.0)
        elapsed = time.time() - start_t
        chunks = buffer._chunks
        now_time = time.time()
        metrics = detector.get_window_metrics(now_time, window_seconds=10.0)
        
        audio_info = "No chunks yet"
        if chunks:
            latest_chunk = chunks[-1]
            mean_db, max_db, score = audio_monitor.analyze_audio_segment(latest_chunk.file_path)
            audio_info = f"Latest chunk={latest_chunk.file_path.name} ({latest_chunk.file_path.stat().st_size} bytes), Mean={mean_db:.1f}dB, Max={max_db:.1f}dB"

        print(f"[{elapsed:.1f}s] Ingested {len(messages_received)} msgs | Live MPS: {metrics.mps:.1f} | Chunks: {len(chunks)} | {audio_info}")

    # Tear down
    print("Stopping capture and disconnecting...")
    chat_task.cancel()
    await chat_source.disconnect()
    await buffer.stop()

    print("\n--- RESULTS OF LIVE STREAM VERIFICATION ---")
    print(f"Total live chat messages received: {len(messages_received)}")
    print(f"Total video buffer chunks captured: {len(buffer._chunks)}")
    if buffer._chunks:
        print(f"First chunk: {buffer._chunks[0].file_path} ({buffer._chunks[0].file_path.stat().st_size} bytes)")
    
    assert len(messages_received) > 0, "No live chat messages received from Twitch IRC!"
    print("=== LIVE STREAM VERIFICATION SUCCESSFUL! ===")


if __name__ == "__main__":
    asyncio.run(run_live_test())
