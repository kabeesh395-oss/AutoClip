"""
Clean unwanted test data from autoclip.sqlite3 and seed a pristine, realistic showcase batch.
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "autoclip.sqlite3"
IST = timezone(timedelta(hours=5, minutes=30))

def now_iso():
    return datetime.now(IST).isoformat()

def clean_and_seed():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON;")

    # 1. Delete all old test batches, jobs, clips, schedules, sources
    print("Purging unwanted test batches and test records...")
    con.execute("DELETE FROM schedules;")
    con.execute("DELETE FROM clips;")
    con.execute("DELETE FROM jobs;")
    con.execute("DELETE FROM sources;")
    con.execute("DELETE FROM batches;")
    con.commit()

    # 2. Insert one clean, pristine showcase batch
    batch_id = "batch_apex_champions_2026"
    now = now_iso()
    con.execute("""
        INSERT INTO batches (
            id, name, status, target_videos_per_day, schedule_days,
            batch_target, daily_shorts, daily_long, timezone, language, whisper_model,
            created_at, updated_at
        ) VALUES (
            ?, 'Apex Champions: Final Round Highlights', 'AWAITING_APPROVAL', 3, 3,
            9, 2, 1, 'Asia/Kolkata', 'en', 'small',
            ?, ?
        )
    """, (batch_id, now, now))

    # 3. Insert real source video
    source_id = "src_apex_championship_1080p"
    con.execute("""
        INSERT INTO sources (
            id, batch_id, source_url, title, duration, game_name, rights_status, created_at
        ) VALUES (
            ?, ?, 'data/sample_real_video.mp4', 'Apex Legends Grand Finals - Championship Clutch',
            8.53, 'Apex Legends', 'PASS', ?
        )
    """, (source_id, batch_id, now))

    # 4. Insert 1 real completed job
    job_id = "job_apex_render_completed"
    con.execute("""
        INSERT INTO jobs (
            id, batch_id, source_id, status, progress, stage_message,
            retry_count, max_retries, updated_at, completed_at
        ) VALUES (
            ?, ?, ?, 'COMPLETED', 100, 'All clips rendered with Fit+Blur & Kinetic Captions',
            0, 3, ?, ?
        )
    """, (job_id, batch_id, source_id, now, now))

    # 5. Insert real 9:16 Short Clip
    short_clip_id = "clip_short_clutch_01"
    short_mp4 = "data/e2e_stage2_output/rendered_short_9x16.mp4"
    short_srt = "data/e2e_stage2_output/rendered_short_9x16.srt"
    con.execute("""
        INSERT INTO clips (
            id, batch_id, source_id, format, start_time, end_time, duration,
            category, hook_score, virality_grade, flow_score, engagement_score, trend_score,
            layout, caption_style, why_viral, improvement_tips_json,
            title, titles_json, description, hashtags_json, hook_json,
            output_path, caption_file, rights_status, approval_status, approved_at, created_at
        ) VALUES (
            ?, ?, ?, 'SHORT', 0.0, 7.67, 7.67,
            'Gaming Clutch', 99.0, 'A+', 94.0, 96.0, 92.0,
            'FIT_BLUR', 'KINETIC_YELLOW',
            'Instant high-energy audio hook within the first 1.5s with rapid-paced visual highlights and bold kinetic subtitles.',
            ?,
            '🔥 UNBELIEVABLE Squad Clutch Play to Secure Victory! 🏆',
            ?,
            'Watch the impossible final round comeback! High-octane clutch moments breakdown.\n\n#ApexLegends #Gaming #Clutch #Shorts',
            ?,
            ?,
            ?, ?, 'PASS', 'APPROVED', ?, ?
        )
    """, (
        short_clip_id, batch_id, source_id,
        json.dumps(["Add high-contrast sound effect on 0.5s beat", "Pace transition into call to action"]),
        json.dumps([
            "🔥 UNBELIEVABLE Squad Clutch Play to Secure Victory! 🏆",
            "They actually won this round?! 🤯",
            "The Most Insane Final Circle Clutch in Apex History",
            "1 HP and a Dream: Apex Legends Championship Round",
            "How Champions Close Out the Tournament"
        ]),
        json.dumps(["#ApexLegends", "#GamingHighlights", "#ClutchMoments", "#Esports", "#Shorts"]),
        json.dumps({"text": "What an unbelievable clutch play by the champion squad!", "score": 99.0}),
        short_mp4, short_srt, now, now
    ))

    # 6. Insert real 16:9 Long-Form Compilation Clip
    long_clip_id = "clip_long_compilation_01"
    long_mp4 = "data/e2e_stage2_output/rendered_compilation_16x9.mp4"
    long_srt = "data/e2e_stage2_output/rendered_compilation_16x9.srt"
    con.execute("""
        INSERT INTO clips (
            id, batch_id, source_id, format, start_time, end_time, duration,
            category, hook_score, virality_grade, flow_score, engagement_score, trend_score,
            layout, caption_style, why_viral, improvement_tips_json,
            title, titles_json, description, hashtags_json, hook_json,
            output_path, caption_file, rights_status, approval_status, approved_at, created_at
        ) VALUES (
            ?, ?, ?, 'LONG', 0.0, 7.67, 7.67,
            'Match Breakdown', 91.0, 'A', 89.0, 90.0, 88.0,
            'LANDSCAPE_16_9', 'STANDARD',
            'Full narrative breakdown with synchronized chapter timestamps and complete tactical overview.',
            ?,
            'Apex Legends: Complete Final Round Championship Breakdown',
            ?,
            'The complete tournament-winning final round breakdown with full player comms and analysis.\n\nTimestamps:\n00:00 - Introduction & Team Positioning\n00:03 - The Game-Winning Clutch Play\n\n#ApexLegends #FullRecap',
            ?,
            ?,
            ?, ?, 'PASS', 'APPROVED', ?, ?
        )
    """, (
        long_clip_id, batch_id, source_id,
        json.dumps(["Include pre-game tactical map overview in opening frame"]),
        json.dumps([
            "Apex Legends: Complete Final Round Championship Breakdown",
            "How The Championship Squad Pulled Off The Impossible",
            "Full Tactical Analysis: Apex Legends Grand Finals"
        ]),
        json.dumps(["#ApexLegends", "#GrandFinals", "#TournamentRecap", "#Esports"]),
        json.dumps({"text": "Welcome to the AutoClip AI studio tactical recap.", "score": 91.0}),
        long_mp4, long_srt, now, now
    ))
    con.commit()
    con.close()

    # 7. Generate clean schedule slots using batch_manager
    sys.path.insert(0, str(ROOT))
    from autoclip_app.batch_manager import generate_batch_schedule_slots
    slots = generate_batch_schedule_slots(batch_id, db_path=DB_PATH)
    print(f"Generated {len(slots)} schedule release slots for approved clips.")
    print("Database successfully cleaned and seeded with 1 high-production showcase batch!")

if __name__ == "__main__":
    clean_and_seed()
