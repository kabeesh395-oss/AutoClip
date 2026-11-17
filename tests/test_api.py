from fastapi.testclient import TestClient
from autoclip_app.app import app, jobs, lock


client = TestClient(app)


def test_api_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["app"] == "AutoClip AI"


def test_api_history_empty():
    response = client.get("/api/history")
    assert response.status_code == 200
    assert "jobs" in response.json()


def test_clip_metadata_regenerate_and_update():
    # Inject mock job
    job_id = "test-job-123"
    with lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "completed",
            "message": "Clips ready",
            "clips": [
                {
                    "id": 1,
                    "title": "Initial Title",
                    "reason": "Initial Reason",
                    "score": 90,
                    "start": 0.0,
                    "end": 25.0,
                    "duration": 25.0,
                    "titles": ["Initial Title 1"],
                    "description": "Initial Description\n\n#Shorts",
                    "hashtags": ["#Shorts", "#Viral"],
                    "hook": {"opening_hook": "Initial Hook", "hook_timestamp": 0.0},
                    "scene_intelligence": {"strongest_scene": "Scene 1"},
                    "subtitles_srt": "1\n00:00:00,000 --> 00:00:25,000\nHello",
                    "video_url": "/outputs/test-job-123/01-clip.mp4",
                    "subtitle_url": "/outputs/test-job-123/01-clip.srt",
                }
            ]
        }

    # Test GET /api/jobs/{job_id}
    res = client.get(f"/api/jobs/{job_id}")
    assert res.status_code == 200
    assert res.json()["id"] == job_id
    assert len(res.json()["clips"]) == 1

    # Test PUT /api/clips/{job_id}/1 (edit)
    update_res = client.put(f"/api/clips/{job_id}/1", json={
        "youtube_title": "Custom Edited Title",
        "description": "New custom description with #Shorts",
        "hashtags": ["#Shorts", "#Custom"]
    })
    assert update_res.status_code == 200
    updated_data = update_res.json()
    assert updated_data["youtube_title"] == "Custom Edited Title"
    assert updated_data["description"] == "New custom description with #Shorts"
    assert updated_data["hashtags"] == ["#Shorts", "#Custom"]

    # Test POST /api/clips/{job_id}/1/regenerate-metadata
    regen_res = client.post(f"/api/clips/{job_id}/1/regenerate-metadata")
    assert regen_res.status_code == 200
    regen_data = regen_res.json()
    assert "hook" in regen_data
    assert len(regen_data["titles"]) == 5
    assert len(regen_data["hashtags"]) >= 4

    # Cleanup mock job
    with lock:
        jobs.pop(job_id, None)


def test_batch_api_endpoints():
    # 1. Create creator
    creator_res = client.post("/api/creators", json={
        "name": "NinjaGaming",
        "channel_url": "https://youtube.com/@NinjaGaming",
        "permission_status": "ACTIVE",
        "commercial_use_status": "ALLOWED"
    })
    assert creator_res.status_code == 200
    creator = creator_res.json()
    assert creator["id"]

    # 2. List creators
    creators_list_res = client.get("/api/creators")
    assert creators_list_res.status_code == 200
    assert any(c["id"] == creator["id"] for c in creators_list_res.json()["creators"])

    # 3. Create batch
    batch_res = client.post("/api/batches", json={
        "name": "3-Day High Energy Batch",
        "target_videos_per_day": 3,
        "schedule_days": 3,
        "daily_shorts": 2,
        "daily_long": 1
    })
    assert batch_res.status_code == 200
    batch = batch_res.json()
    assert batch["batch_target"] == 9
    batch_id = batch["id"]

    # 3b. Register verified game policy
    policy_res = client.post("/api/game-policies", json={
        "game_name": "Valorant",
        "publisher_name": "Riot Games",
        "commercial_policy": "VERIFIED",
        "monetization_allowed": 1
    })
    assert policy_res.status_code == 200

    # 4. Add source to batch
    source_res = client.post(f"/api/batches/{batch_id}/sources", json={
        "source_url": "https://youtube.com/watch?v=apibatchtest1",
        "creator_id": creator["id"],
        "game_name": "Valorant",
        "title": "Valorant Finals Match"
    })
    assert source_res.status_code == 200
    assert source_res.json()["rights_status"] == "PASS"

    # 5. Get batch details
    get_batch_res = client.get(f"/api/batches/{batch_id}")
    assert get_batch_res.status_code == 200
    batch_details = get_batch_res.json()
    assert len(batch_details["sources"]) == 1
    assert len(batch_details["jobs"]) == 1

    # 6. Get batch report
    report_res = client.get(f"/api/batches/{batch_id}/report")
    assert report_res.status_code == 200
    report = report_res.json()
    assert report["sources_imported"] == 1
    assert report["target_batch"] == 9

    # 7. Start batch
    start_res = client.post(f"/api/batches/{batch_id}/start")
    assert start_res.status_code == 200
    assert start_res.json()["status"] == "started"

    # 8. Retry & Cancel job endpoints
    job_id = batch_details["jobs"][0]["id"]
    cancel_res = client.post(f"/api/jobs/{job_id}/cancel")
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "CANCELLED"

    retry_res = client.post(f"/api/jobs/{job_id}/retry")
    assert retry_res.status_code == 200
    assert retry_res.json()["status"] == "QUEUED"

    # 9. Get batch clips (initially empty)
    clips_res = client.get(f"/api/batches/{batch_id}/clips")
    assert clips_res.status_code == 200
    assert "clips" in clips_res.json()

    # 10. Server Info endpoint
    info_res = client.get("/api/server-info")
    assert info_res.status_code == 200
    info = info_res.json()
    assert "local_ip" in info
    assert info["port"] == 8765
    assert info["default_batch_config"]["batch_target"] == 9
    assert info["apk_download_url"] == "/api/download-apk"


def test_accessibility_and_file_transfer_endpoints():
    # 1. Test Static files
    static_js_res = client.get("/static/qrcode.min.js")
    assert static_js_res.status_code == 200
    assert "QRCode" in static_js_res.text

    static_html_res = client.get("/static/index.html")
    assert static_html_res.status_code == 200
    assert "modalMobilePairing" in static_html_res.text
    assert "sourceDropzone" in static_html_res.text
    assert "btnExportZip" in static_html_res.text

    # 2. Test Download APK endpoint
    from autoclip_app.app import ROOT
    apk_path = ROOT / "android-app" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    apk_path.parent.mkdir(parents=True, exist_ok=True)
    created_dummy_apk = False
    if not apk_path.exists():
        apk_path.write_bytes(b"PK\x03\x04dummy_android_package")
        created_dummy_apk = True

    try:
        apk_res = client.get("/api/download-apk")
        assert apk_res.status_code == 200
        assert apk_res.headers["content-type"] == "application/vnd.android.package-archive"
    finally:
        if created_dummy_apk and apk_path.exists():
            try:
                apk_path.unlink()
            except Exception:
                pass

    # 3. Test Batch Source Upload
    batch_res = client.post("/api/batches", json={
        "name": "Upload Test Batch",
        "target_videos_per_day": 3,
        "schedule_days": 3,
        "daily_shorts": 2,
        "daily_long": 1
    })
    batch_id = batch_res.json()["id"]

    dummy_video_bytes = b"fake video content for testing drag-and-drop upload"
    upload_res = client.post(
        f"/api/batches/{batch_id}/upload-source",
        files={"file": ("test_gameplay.mp4", dummy_video_bytes, "video/mp4")},
        data={"game_name": "Valorant"}
    )
    assert upload_res.status_code == 200
    upload_data = upload_res.json()
    assert upload_data["id"]
    assert upload_data["batch_id"] == batch_id

    # 4. Test Download ZIP (when no approved clips yet -> 400 Bad Request)
    zip_empty_res = client.get(f"/api/batches/{batch_id}/download-zip")
    assert zip_empty_res.status_code == 400

    # 5. Add a mock approved clip into database and test Download ZIP success
    import uuid
    clip_id = f"clip_{uuid.uuid4().hex[:8]}"
    from autoclip_app.batch_manager import get_db, _now_ist_iso
    with get_db() as conn:
        conn.execute("""
            INSERT INTO clips (
                id, batch_id, source_id, format, start_time, end_time, duration,
                title, description, hashtags_json, hook_score,
                approval_status, output_path, created_at
            ) VALUES (
                ?, ?, ?, 'SHORT', 0.0, 15.0, 15.0,
                'Victory Moment', 'Epic win #Shorts', '["#Shorts"]', 95.0,
                'APPROVED', '', ?
            )
        """, (clip_id, batch_id, upload_data["id"], _now_ist_iso()))
        conn.commit()

    zip_res = client.get(f"/api/batches/{batch_id}/download-zip")
    assert zip_res.status_code == 200
    assert zip_res.headers["content-type"] == "application/zip"
    assert "attachment; filename=" in zip_res.headers["content-disposition"]


def test_opus_clip_rerender_endpoint(tmp_path):
    import subprocess
    import uuid
    from autoclip_app.batch_manager import get_db, _now_ist_iso, create_batch, add_source_to_batch

    synth_video = tmp_path / "rerender_src.mp4"
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=2,format=yuv420p",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-c:a", "aac", str(synth_video)
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    batch = create_batch("Opus Test Batch", 3, 3, 2, 1)
    source = add_source_to_batch(batch["id"], str(synth_video), file_path=str(synth_video))

    clip_id = f"clip_{uuid.uuid4().hex[:8]}"
    out_dir = tmp_path / "outputs" / "batch_test" / clip_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{clip_id}.mp4"

    with get_db() as conn:
        conn.execute("""
            INSERT INTO clips (
                id, batch_id, source_id, format, start_time, end_time, duration,
                title, description, hashtags_json, hook_score,
                approval_status, output_path, created_at, layout, caption_style
            ) VALUES (
                ?, ?, ?, 'SHORT', 0.0, 1.5, 1.5,
                'Opus Rerender Test', 'Testing re-render', '["#Shorts"]', 90.0,
                'PENDING', ?, ?, 'FIT_BLUR', 'KINETIC_YELLOW'
            )
        """, (clip_id, batch["id"], source["id"], str(out_file), _now_ist_iso()))
        conn.commit()

    res = client.post(f"/api/clips/{clip_id}/re-render", json={
        "layout": "SMART_CROP",
        "caption_style": "NEON_MINT",
        "show_hook_header": True
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "re-rendered"
    assert data["layout"] == "SMART_CROP"
    assert data["caption_style"] == "NEON_MINT"
    assert out_file.exists()
    assert out_file.with_suffix(".ass").exists()


def test_server_logs_endpoint():
    res = client.get("/api/server-logs")
    assert res.status_code == 200
    data = res.json()
    assert "logs" in data
    assert isinstance(data["logs"], list)


def test_local_worker_poll_and_complete_endpoints():
    import uuid
    from autoclip_app.batch_manager import get_db, _now_ist_iso, create_batch, add_source_to_batch

    batch = create_batch("Worker Polling Test Batch", 3, 3, 2, 1)
    source = add_source_to_batch(batch["id"], "https://example.com/test_video.mp4")

    # 1. Test Worker Poll (picks up queued job for this batch)
    poll_res = client.post("/api/worker/poll", json={"worker_id": "test-runner-1", "batch_id": batch["id"]})
    assert poll_res.status_code == 200
    poll_data = poll_res.json()
    assert poll_data["has_job"] is True
    job_id = poll_data["job"]["id"]
    assert poll_data["job"]["batch_id"] == batch["id"]

    # 2. Test Worker Complete
    clips_payload = [
        {
            "batch_id": batch["id"],
            "source_id": source["id"],
            "format": "SHORT",
            "start_time": 0.0,
            "end_time": 15.0,
            "duration": 15.0,
            "title": "Cloud Rendered Viral Short",
            "titles": ["Cloud Rendered Viral Short", "Alternative Title"],
            "description": "Rendered via Local PC Worker #Shorts",
            "hashtags": ["#Shorts", "#Viral"],
            "hook": {"opening_hook": "Watch this insane play"},
            "hook_score": 92.0,
            "virality_grade": "A+",
            "flow_score": 88.0,
            "engagement_score": 90.0,
            "trend_score": 85.0,
            "why_viral": "Fast pacing and emotional hook",
            "improvement_tips": ["Add zoom cut at 5s"],
            "layout": "FIT_BLUR",
            "caption_style": "KINETIC_YELLOW",
            "local_video_path": "E:/autoclip/AutoClip/data/outputs/test.mp4",
        }
    ]

    complete_res = client.post(f"/api/worker/jobs/{job_id}/complete", json={"clips": clips_payload})
    assert complete_res.status_code == 200
    comp_data = complete_res.json()
    assert comp_data["status"] == "COMPLETED"
    assert comp_data["clips_created"] == 1

    # 3. Verify in DB
    with get_db() as conn:
        job_row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert job_row["status"] == "COMPLETED"
        clip_row = conn.execute("SELECT * FROM clips WHERE batch_id = ?", (batch["id"],)).fetchone()
        assert clip_row is not None
        assert clip_row["virality_grade"] == "A+"
        assert clip_row["approval_status"] == "PENDING"

