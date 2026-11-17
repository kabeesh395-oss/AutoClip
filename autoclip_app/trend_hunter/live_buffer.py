from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("autoclip.trend_hunter.live_buffer")

ROOT = Path(__file__).resolve().parents[2]
BUFFERS_DIR = ROOT / "data" / "live_buffers"


@dataclass
class BufferChunk:
    chunk_index: int
    file_path: Path
    start_time: float  # Stream-relative seconds
    end_time: float
    created_at: float  # Wall-clock time


class RollingLiveBuffer:
    """
    Continuous circular rolling video buffer for live stream monitoring.
    Maintains segmented video chunks on disk (e.g., 5-second intervals),
    retains a configurable rolling window (e.g. 5 minutes) so that pre-roll
    footage (20 seconds prior to a viral event) is never lost, and waits for
    post-roll footage (40 seconds after) to be captured before extracting clips.
    """

    def __init__(
        self,
        session_id: str,
        retention_seconds: float = 300.0,
        segment_seconds: float = 5.0,
        buffer_dir: Path | None = None,
    ) -> None:
        self.session_id = session_id
        self.retention_seconds = retention_seconds
        self.segment_seconds = segment_seconds
        self.buffer_dir = buffer_dir or (BUFFERS_DIR / session_id)
        self.buffer_dir.mkdir(parents=True, exist_ok=True)

        self._chunks: list[BufferChunk] = []
        self._process: subprocess.Popen | None = None
        self._is_recording = False
        self._stream_start_wall_time: float = 0.0
        self._lock = asyncio.Lock()
        self._next_chunk_idx = 0

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def current_buffer_duration(self) -> float:
        if not self._chunks:
            return 0.0
        return max(0.0, self._chunks[-1].end_time - self._chunks[0].start_time)

    @property
    def latest_timestamp(self) -> float:
        if not self._chunks:
            return 0.0
        return self._chunks[-1].end_time

    async def start_capture(
        self,
        stream_url: str,
        is_simulation: bool = False,
        simulated_source: Path | None = None,
    ) -> None:
        """
        Starts live buffer ingestion via Streamlink/FFmpeg or simulated source.
        """
        self._stream_start_wall_time = time.time()
        self._is_recording = True

        if is_simulation:
            # Simulation mode: slice or replay simulated source chunks
            asyncio.create_task(self._run_simulated_capture(simulated_source))
            return

        # Real live capture: Try streamlink pipe to ffmpeg, fallback to direct ffmpeg
        segment_pattern = str(self.buffer_dir / "chunk_%04d.ts")
        streamlink_cmd = [
            "streamlink",
            "--stdout",
            stream_url,
            "best",
        ]
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-i", "pipe:0",
            "-c", "copy",
            "-f", "segment",
            "-segment_time", str(int(self.segment_seconds)),
            "-reset_timestamps", "1",
            segment_pattern,
        ]

        logger.info("Starting live buffer capture for session %s on %s", self.session_id, stream_url)
        try:
            # Launch streamlink piped to ffmpeg
            sl_proc = subprocess.Popen(
                streamlink_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            ff_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdin=sl_proc.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._process = ff_proc
            # Start watcher task to register new chunks as they are written
            asyncio.create_task(self._monitor_buffer_files())
        except Exception as exc:
            logger.warning("Streamlink pipe failed, trying direct FFmpeg: %s", exc)
            direct_ffmpeg = [
                "ffmpeg", "-y",
                "-i", stream_url,
                "-c", "copy",
                "-f", "segment",
                "-segment_time", str(int(self.segment_seconds)),
                "-reset_timestamps", "1",
                segment_pattern,
            ]
            self._process = subprocess.Popen(direct_ffmpeg, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            asyncio.create_task(self._monitor_buffer_files())

    async def _monitor_buffer_files(self) -> None:
        """Continuously indexes newly completed segment files and evicts expired chunks."""
        while self._is_recording:
            await asyncio.sleep(2.0)
            files = sorted(self.buffer_dir.glob("chunk_*.ts"))
            # Leave the latest file alone as it might be currently writing
            completed_files = files[:-1] if len(files) > 1 else files
            for f in completed_files:
                if not any(c.file_path == f for c in self._chunks):
                    # Check if file has non-zero size
                    try:
                        if f.stat().st_size > 1024:
                            start_t = self._next_chunk_idx * self.segment_seconds
                            end_t = start_t + self.segment_seconds
                            chunk = BufferChunk(
                                chunk_index=self._next_chunk_idx,
                                file_path=f,
                                start_time=start_t,
                                end_time=end_t,
                                created_at=time.time(),
                            )
                            self._chunks.append(chunk)
                            self._next_chunk_idx += 1
                    except Exception:
                        pass

            # Evict chunks older than retention window
            await self._prune_expired_chunks()

    async def _run_simulated_capture(self, source_path: Path | None) -> None:
        """Generates mock/simulated chunks for testing and verification."""
        chunk_idx = 0
        while self._is_recording:
            start_t = chunk_idx * self.segment_seconds
            end_t = start_t + self.segment_seconds
            dummy_file = self.buffer_dir / f"chunk_{chunk_idx:04d}.ts"
            if not dummy_file.exists():
                # Write minimal valid dummy byte data
                dummy_file.write_bytes(b"\x47\x40\x00\x10" * 256)

            chunk = BufferChunk(
                chunk_index=chunk_idx,
                file_path=dummy_file,
                start_time=start_t,
                end_time=end_t,
                created_at=time.time(),
            )
            self._chunks.append(chunk)
            chunk_idx += 1
            await self._prune_expired_chunks()
            await asyncio.sleep(self.segment_seconds)

    async def _prune_expired_chunks(self) -> None:
        """Ensures the buffer retains at least `retention_seconds` of recent footage."""
        if not self._chunks:
            return
        latest_t = self._chunks[-1].end_time
        retention_cutoff = latest_t - self.retention_seconds

        surviving: list[BufferChunk] = []
        for c in self._chunks:
            if c.end_time < retention_cutoff:
                # Remove file from disk to preserve local disk space
                try:
                    if c.file_path.exists():
                        c.file_path.unlink()
                except Exception:
                    pass
            else:
                surviving.append(c)
        self._chunks = surviving

    async def wait_for_post_roll(
        self,
        target_timestamp: float,
        timeout_seconds: float = 60.0,
    ) -> bool:
        """
        Asynchronously waits until post-roll footage has elapsed and been written to the buffer.
        """
        start_wait = time.time()
        while time.time() - start_wait < timeout_seconds:
            if self.latest_timestamp >= target_timestamp:
                return True
            await asyncio.sleep(1.0)
        return self.latest_timestamp >= target_timestamp

    def extract_segment(
        self,
        start_seconds: float,
        end_seconds: float,
        output_path: Path,
        full_source_fallback: Path | None = None,
    ) -> Path:
        """
        Extracts a clean, seamless video clip covering [start_seconds, end_seconds].
        Stitches required buffer chunks or cuts from full source fallback.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        duration = max(1.0, end_seconds - start_seconds)

        # 1. If full source fallback is provided and exists, use direct ffmpeg seek
        if full_source_fallback and Path(full_source_fallback).exists():
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(round(start_seconds, 2)),
                "-i", str(full_source_fallback),
                "-t", str(round(duration, 2)),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-c:a", "aac", "-b:a", "192k",
                str(output_path),
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                return output_path

        # 2. Extract from overlapping live buffer chunks
        overlapping_chunks = [
            c for c in self._chunks
            if c.end_time > start_seconds and c.start_time < end_seconds and c.file_path.exists()
        ]

        if not overlapping_chunks:
            raise RuntimeError(f"No buffer chunks found overlapping interval [{start_seconds:.1f}, {end_seconds:.1f}]")

        # Concat list file
        concat_list = self.buffer_dir / f"concat_{int(start_seconds)}_{int(end_seconds)}.txt"
        lines = [f"file '{c.file_path.resolve()}'" for c in overlapping_chunks]
        concat_list.write_text("\n".join(lines), encoding="utf-8")

        # Stitch and trim relative offset
        rel_start = max(0.0, start_seconds - overlapping_chunks[0].start_time)
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-ss", str(round(rel_start, 2)),
            "-i", str(concat_list),
            "-t", str(round(duration, 2)),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            str(output_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            concat_list.unlink()
        except Exception:
            pass

        if res.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError(f"FFmpeg live buffer extraction failed: {res.stderr.decode('utf-8', errors='ignore')[-300:]}")

        return output_path

    async def stop(self) -> None:
        """Stops live buffer capture process and cleans up."""
        self._is_recording = False
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass
            self._process = None

    def purge_buffer(self) -> None:
        """Deletes all buffer files for this session."""
        try:
            if self.buffer_dir.exists():
                shutil.rmtree(self.buffer_dir)
        except Exception as exc:
            logger.warning("Error purging buffer directory %s: %s", self.buffer_dir, exc)
