-- AutoClip Cloudflare D1 Database Schema
-- Compatible with SQLite & AutoClip batch_manager

CREATE TABLE IF NOT EXISTS creators (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    channel_url TEXT NOT NULL,
    permission_status TEXT NOT NULL DEFAULT 'ACTIVE',
    permission_document TEXT,
    permitted_platforms TEXT NOT NULL DEFAULT 'youtube',
    commercial_use_status TEXT NOT NULL DEFAULT 'ALLOWED',
    attribution_requirements TEXT,
    expiration_date TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS game_policies (
    game_name TEXT PRIMARY KEY,
    publisher_name TEXT,
    commercial_policy TEXT NOT NULL DEFAULT 'VERIFIED',
    monetization_allowed INTEGER NOT NULL DEFAULT 1,
    verified_at TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    target_videos_per_day INTEGER NOT NULL DEFAULT 3,
    schedule_days INTEGER NOT NULL DEFAULT 3,
    batch_target INTEGER NOT NULL DEFAULT 9,
    daily_shorts INTEGER NOT NULL DEFAULT 2,
    daily_long INTEGER NOT NULL DEFAULT 1,
    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    language TEXT NOT NULL DEFAULT 'en',
    whisper_model TEXT NOT NULL DEFAULT 'small',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    creator_id TEXT,
    source_url TEXT NOT NULL,
    file_path TEXT,
    title TEXT,
    duration REAL DEFAULT 0.0,
    game_name TEXT,
    rights_status TEXT NOT NULL DEFAULT 'PASS',
    rights_notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE,
    FOREIGN KEY (creator_id) REFERENCES creators(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    source_id TEXT,
    job_type TEXT NOT NULL DEFAULT 'SOURCE_PROCESS',
    status TEXT NOT NULL DEFAULT 'QUEUED',
    progress INTEGER NOT NULL DEFAULT 0,
    stage_message TEXT DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    error_message TEXT DEFAULT '',
    started_at TEXT,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    worker_id TEXT DEFAULT '',
    FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'SHORT',
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    duration REAL NOT NULL,
    category TEXT DEFAULT 'Highlight',
    hook_score REAL NOT NULL DEFAULT 0.0,
    title TEXT NOT NULL,
    titles_json TEXT,
    description TEXT,
    hashtags_json TEXT,
    hook_json TEXT,
    scene_intelligence_json TEXT,
    transcript TEXT,
    caption_file TEXT,
    output_path TEXT,
    r2_video_key TEXT,
    r2_subtitle_key TEXT,
    r2_ass_key TEXT,
    rights_status TEXT NOT NULL DEFAULT 'PASS',
    approval_status TEXT NOT NULL DEFAULT 'PENDING',
    approved_at TEXT,
    created_at TEXT NOT NULL,
    virality_grade TEXT DEFAULT 'A',
    flow_score REAL DEFAULT 80.0,
    engagement_score REAL DEFAULT 85.0,
    trend_score REAL DEFAULT 80.0,
    layout TEXT DEFAULT 'FIT_BLUR',
    caption_style TEXT DEFAULT 'KINETIC_YELLOW',
    why_viral TEXT DEFAULT '',
    improvement_tips_json TEXT DEFAULT '[]',
    FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    clip_id TEXT NOT NULL,
    slot_number INTEGER NOT NULL,
    scheduled_at TEXT NOT NULL,
    slot_time TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'SHORT',
    status TEXT NOT NULL DEFAULT 'PENDING',
    youtube_channel_id TEXT,
    youtube_video_id TEXT,
    youtube_privacy_status TEXT DEFAULT 'private',
    published_at TEXT,
    error_message TEXT,
    FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE,
    FOREIGN KEY (clip_id) REFERENCES clips(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT,
    user_id TEXT DEFAULT 'admin',
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    details TEXT
);
