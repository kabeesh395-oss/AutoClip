from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from .models import ChatMessage, SpikeEvent

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DEFAULT_DB = DATA / "autoclip.sqlite3"


@contextmanager
def get_chat_db(db_path: Path = DEFAULT_DB) -> Generator[sqlite3.Connection, None, None]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_chat_tables(db_path: Path = DEFAULT_DB) -> None:
    with get_chat_db(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                stream_url TEXT,
                source_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                started_at TEXT NOT NULL,
                ended_at TEXT,
                total_messages INTEGER NOT NULL DEFAULT 0,
                total_spikes INTEGER NOT NULL DEFAULT 0,
                meta_json TEXT
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp_sec REAL NOT NULL,
                author TEXT NOT NULL,
                text TEXT NOT NULL,
                emotes_json TEXT,
                is_bot INTEGER NOT NULL DEFAULT 0,
                is_spike INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_msg_session ON chat_messages(session_id, timestamp_sec);")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_spikes (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                start_time REAL NOT NULL,
                peak_time REAL NOT NULL,
                end_time REAL NOT NULL,
                baseline_mps REAL NOT NULL,
                peak_mps REAL NOT NULL,
                z_score REAL NOT NULL,
                confidence_score REAL NOT NULL,
                dominant_emotes_json TEXT,
                dominant_keywords_json TEXT,
                clip_start REAL NOT NULL,
                clip_end REAL NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                clip_path TEXT,
                status TEXT NOT NULL DEFAULT 'DETECTED',
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_spikes_session ON chat_spikes(session_id, peak_time);")


def create_chat_session(session_id: str, stream_url: str, source_type: str, meta: dict[str, Any] | None = None, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    init_chat_tables(db_path)
    started_at = datetime.now(timezone.utc).isoformat()
    with get_chat_db(db_path) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO chat_sessions (id, stream_url, source_type, status, started_at, total_messages, total_spikes, meta_json)
            VALUES (?, ?, ?, 'ACTIVE', ?, 0, 0, ?)
        """, (session_id, stream_url, source_type, started_at, json.dumps(meta or {})))
    return {"id": session_id, "stream_url": stream_url, "source_type": source_type, "status": "ACTIVE", "started_at": started_at}


def close_chat_session(session_id: str, db_path: Path = DEFAULT_DB) -> None:
    ended_at = datetime.now(timezone.utc).isoformat()
    with get_chat_db(db_path) as conn:
        conn.execute("""
            UPDATE chat_sessions
            SET status = 'COMPLETED', ended_at = ?
            WHERE id = ?
        """, (ended_at, session_id))


def insert_chat_message(session_id: str, msg: ChatMessage, is_spike: bool = False, db_path: Path = DEFAULT_DB) -> None:
    insert_chat_messages_batch(session_id, [(msg, is_spike)], db_path=db_path)


def insert_chat_messages_batch(session_id: str, items: list[tuple[ChatMessage, bool]], db_path: Path = DEFAULT_DB) -> None:
    if not items:
        return
    with get_chat_db(db_path) as conn:
        conn.executemany("""
            INSERT INTO chat_messages (session_id, timestamp_sec, author, text, emotes_json, is_bot, is_spike)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                session_id,
                m.timestamp,
                m.author,
                m.text,
                json.dumps(m.emotes),
                1 if m.is_bot else 0,
                1 if is_spk else 0
            )
            for m, is_spk in items
        ])
        conn.execute("""
            UPDATE chat_sessions
            SET total_messages = total_messages + ?
            WHERE id = ?
        """, (len(items), session_id))


def insert_chat_spike(spike: SpikeEvent, db_path: Path = DEFAULT_DB) -> None:
    with get_chat_db(db_path) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO chat_spikes (
                id, session_id, start_time, peak_time, end_time, baseline_mps, peak_mps,
                z_score, confidence_score, dominant_emotes_json, dominant_keywords_json,
                clip_start, clip_end, title, description, clip_path, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            spike.spike_id,
            spike.session_id,
            spike.start_time,
            spike.peak_time,
            spike.end_time,
            spike.baseline_mps,
            spike.peak_mps,
            spike.z_score,
            spike.confidence_score,
            json.dumps(spike.dominant_emotes),
            json.dumps(spike.dominant_keywords),
            spike.clip_start,
            spike.clip_end,
            spike.title,
            spike.description,
            spike.clip_path,
            spike.status,
            spike.created_at
        ))
        conn.execute("""
            UPDATE chat_sessions
            SET total_spikes = total_spikes + 1
            WHERE id = ?
        """, (spike.session_id,))


def update_spike_clip_path(spike_id: str, clip_path: str, status: str = "EXTRACTED", db_path: Path = DEFAULT_DB) -> None:
    with get_chat_db(db_path) as conn:
        conn.execute("""
            UPDATE chat_spikes
            SET clip_path = ?, status = ?
            WHERE id = ?
        """, (clip_path, status, spike_id))


def list_chat_spikes(session_id: str | None = None, limit: int = 50, db_path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    init_chat_tables(db_path)
    with get_chat_db(db_path) as conn:
        if session_id:
            rows = conn.execute("""
                SELECT * FROM chat_spikes
                WHERE session_id = ?
                ORDER BY peak_time DESC
                LIMIT ?
            """, (session_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM chat_spikes
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        d["dominant_emotes"] = json.loads(d.get("dominant_emotes_json") or "[]")
        d["dominant_keywords"] = json.loads(d.get("dominant_keywords_json") or "[]")
        d["duration"] = round(max(0.0, d.get("clip_end", 0.0) - d.get("clip_start", 0.0)), 2)
        out.append(d)
    return out


def get_chat_session_summary(session_id: str, db_path: Path = DEFAULT_DB) -> dict[str, Any] | None:
    init_chat_tables(db_path)
    with get_chat_db(db_path) as conn:
        s_row = conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
        if not s_row:
            return None
        session = dict(s_row)
        session["meta"] = json.loads(session.get("meta_json") or "{}")

        spikes = conn.execute("SELECT * FROM chat_spikes WHERE session_id = ? ORDER BY peak_time ASC", (session_id,)).fetchall()
        session["spikes"] = [dict(sp) for sp in spikes]
        return session
