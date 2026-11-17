from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .models import JobQueueItem
from .storage import DEFAULT_DB, get_trend_db

logger = logging.getLogger("autoclip.trend_hunter.queue")


def init_queue_tables(db_path: Path = DEFAULT_DB) -> None:
    with get_trend_db(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trend_jobs_queue (
                job_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                clip_id TEXT NOT NULL,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'QUEUED',
                priority INTEGER NOT NULL DEFAULT 10,
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 3,
                payload_json TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)


class ResourceGuard:
    """
    Lightweight resource monitoring & guard.
    Checks CPU & RAM availability before starting intensive FFmpeg encodes
    to prevent system lockups or excessive resource consumption.
    """

    def __init__(self, max_cpu_percent: float = 85.0, max_ram_percent: float = 90.0) -> None:
        self.max_cpu_percent = max_cpu_percent
        self.max_ram_percent = max_ram_percent

    def can_process_job(self) -> bool:
        """Returns True if system resources are within safe operating limits."""
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.1)
            ram = psutil.virtual_memory().percent
            if cpu > self.max_cpu_percent or ram > self.max_ram_percent:
                logger.warning("ResourceGuard: system load high (CPU: %.1f%%, RAM: %.1f%%); pausing worker", cpu, ram)
                return False
        except ImportError:
            # psutil not strictly required; allow processing
            pass
        return True


class PersistentJobQueue:
    """
    Persistent SQLite Job Queue with automated retry, priority dispatching,
    and crash recovery.
    """

    def __init__(self, db_path: Path = DEFAULT_DB, resource_guard: ResourceGuard | None = None) -> None:
        self.db_path = db_path
        self.resource_guard = resource_guard or ResourceGuard()
        init_queue_tables(self.db_path)
        self._handlers: dict[str, Callable[[JobQueueItem], Any]] = {}
        self._is_running = False
        self._worker_task: asyncio.Task | None = None

    def register_handler(self, job_type: str, handler: Callable[[JobQueueItem], Any]) -> None:
        self._handlers[job_type.upper()] = handler

    def enqueue(
        self,
        session_id: str,
        clip_id: str,
        job_type: str,
        payload: dict[str, Any] | None = None,
        priority: int = 10,
    ) -> JobQueueItem:
        """Adds a new job to the SQLite persistent queue."""
        job_id = f"job_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        item = JobQueueItem(
            job_id=job_id,
            session_id=session_id,
            clip_id=clip_id,
            job_type=job_type.upper(),
            status="QUEUED",
            priority=priority,
            payload=payload or {},
            created_at=now,
            updated_at=now,
        )

        with get_trend_db(self.db_path) as conn:
            conn.execute("""
                INSERT INTO trend_jobs_queue (
                    job_id, session_id, clip_id, job_type, status, priority,
                    retry_count, max_retries, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 3, ?, ?, ?)
            """, (
                item.job_id, item.session_id, item.clip_id, item.job_type,
                item.status, item.priority, json.dumps(item.payload),
                item.created_at, item.updated_at,
            ))

        logger.info("Enqueued %s job: %s for clip %s", item.job_type, item.job_id, item.clip_id)
        return item

    def get_pending_jobs(self, limit: int = 10) -> list[JobQueueItem]:
        with get_trend_db(self.db_path) as conn:
            rows = conn.execute("""
                SELECT * FROM trend_jobs_queue
                WHERE status = 'QUEUED'
                ORDER BY priority ASC, created_at ASC
                LIMIT ?
            """, (limit,)).fetchall()
            return [self._row_to_item(r) for r in rows]

    def update_job_status(
        self,
        job_id: str,
        status: str,
        error_message: str | None = None,
        increment_retry: bool = False,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with get_trend_db(self.db_path) as conn:
            if increment_retry:
                if error_message is not None:
                    conn.execute("""
                        UPDATE trend_jobs_queue SET
                            status = ?, error_message = ?, retry_count = retry_count + 1, updated_at = ?
                        WHERE job_id = ?
                    """, (status, error_message, now, job_id))
                else:
                    conn.execute("""
                        UPDATE trend_jobs_queue SET
                            status = ?, retry_count = retry_count + 1, updated_at = ?
                        WHERE job_id = ?
                    """, (status, now, job_id))
            else:
                if error_message is not None:
                    conn.execute("""
                        UPDATE trend_jobs_queue SET
                            status = ?, error_message = ?, updated_at = ?
                        WHERE job_id = ?
                    """, (status, error_message, now, job_id))
                else:
                    conn.execute("""
                        UPDATE trend_jobs_queue SET
                            status = ?, updated_at = ?
                        WHERE job_id = ?
                    """, (status, now, job_id))

    def start_worker(self) -> None:
        if self._is_running:
            return
        self._is_running = True
        self._worker_task = asyncio.create_task(self._process_queue_loop())
        logger.info("PersistentJobQueue worker started")

    def stop_worker(self) -> None:
        self._is_running = False
        if self._worker_task:
            self._worker_task.cancel()
            self._worker_task = None

    async def _process_queue_loop(self) -> None:
        while self._is_running:
            try:
                # Check system resources before picking up heavy tasks
                if not self.resource_guard.can_process_job():
                    await asyncio.sleep(4.0)
                    continue

                jobs = self.get_pending_jobs(limit=1)
                if not jobs:
                    await asyncio.sleep(1.5)
                    continue

                job = jobs[0]
                handler = self._handlers.get(job.job_type)
                if not handler:
                    self.update_job_status(job.job_id, "FAILED", error_message=f"No handler registered for {job.job_type}")
                    continue

                self.update_job_status(job.job_id, "PROCESSING")
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(job)
                    else:
                        handler(job)
                    self.update_job_status(job.job_id, "COMPLETED")
                except Exception as exc:
                    logger.error("Error executing job %s (%s): %s", job.job_id, job.job_type, exc)
                    if job.retry_count + 1 >= job.max_retries:
                        self.update_job_status(job.job_id, "FAILED", error_message=str(exc), increment_retry=True)
                    else:
                        self.update_job_status(job.job_id, "QUEUED", error_message=str(exc), increment_retry=True)

            except asyncio.CancelledError:
                break
            except Exception as loop_exc:
                logger.error("Queue worker unexpected error: %s", loop_exc)
                await asyncio.sleep(2.0)

    def _row_to_item(self, r: Any) -> JobQueueItem:
        payload = {}
        if r["payload_json"]:
            try:
                payload = json.loads(r["payload_json"])
            except Exception:
                pass
        return JobQueueItem(
            job_id=r["job_id"],
            session_id=r["session_id"],
            clip_id=r["clip_id"],
            job_type=r["job_type"],
            status=r["status"],
            priority=r["priority"],
            retry_count=r["retry_count"],
            max_retries=r["max_retries"],
            payload=payload,
            error_message=r["error_message"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
