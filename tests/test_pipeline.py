from autoclip_app.pipeline import Segment, _srt_stamp, choose_highlights


def test_srt_stamp_uses_milliseconds():
    assert _srt_stamp(61.234) == "00:01:01,234"


def test_choose_highlights_validates_and_ranks_candidates(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": '[{"start_seconds":0,"end_seconds":25,"title":"Strong hook","reason":"Clear insight","score":92},'
                               '{"start_seconds":10,"end_seconds":40,"title":"Overlap","score":99},'
                               '{"start_seconds":30,"end_seconds":120,"title":"Too long","score":100}]'}

    monkeypatch.setattr("autoclip_app.pipeline.requests.post", lambda *a, **k: FakeResponse())
    rows = [Segment(0, 10, "Opening idea."), Segment(10, 25, "Complete explanation."),
            Segment(25, 40, "Another idea.")]
    clips = choose_highlights(rows)
    assert len(clips) == 1
    assert clips[0].title == "Strong hook"
    assert clips[0].score == 92


def test_choose_highlights_rejects_empty_transcript():
    try:
        choose_highlights([])
    except ValueError as exc:
        assert "empty" in str(exc).lower()
    else:
        raise AssertionError("Empty transcript must be rejected.")


def test_choose_highlights_standalone_fallback_when_ollama_offline(monkeypatch):
    import requests
    def unavailable(*args, **kwargs):
        raise requests.RequestException("Ollama is not running")

    monkeypatch.setattr("autoclip_app.pipeline.requests.post", unavailable)
    rows = [
        Segment(0.0, 10.0, "Welcome everyone to the championship match!"),
        Segment(10.0, 25.0, "He makes an insane play and gets the clutch win!"),
        Segment(25.0, 35.0, "Unbelievable moment from the team."),
        Segment(35.0, 50.0, "That was an epic final round victory.")
    ]
    clips = choose_highlights(rows, count=2, min_seconds=20, max_seconds=45)
    assert len(clips) >= 1
    assert clips[0].score >= 50.0
    assert clips[0].start >= 0.0
    assert clips[0].end <= 50.5


def test_generate_metadata_has_local_fallback(monkeypatch):
    from autoclip_app.pipeline import Candidate, generate_metadata

    def unavailable(*args, **kwargs):
        raise RuntimeError("local Ollama unavailable")

    monkeypatch.setattr("autoclip_app.pipeline.requests.post", unavailable)
    clip = Candidate(0, 25, "Useful Python tip", "Explains a shortcut", 90,
                     "A useful Python shortcut saves time.")
    metadata = generate_metadata(clip)
    assert metadata["title"] == "Useful Python tip"
    assert "#Shorts" in metadata["description"]
    assert isinstance(metadata["tags"], list)


def test_generate_viral_intelligence_features(monkeypatch):
    from autoclip_app.pipeline import Candidate, generate_viral_intelligence

    def unavailable(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr("autoclip_app.pipeline.requests.post", unavailable)
    clip = Candidate(10.0, 35.0, "Boost Android Performance", "How to optimize compose renders", 95,
                     "Recomposition can slow down your app unless you use remember and derivedStateOf.")
    intel = generate_viral_intelligence(clip)
    
    # 1. AI Hook
    assert "hook" in intel
    assert intel["hook"]["opening_hook"]
    assert intel["hook"]["hook_timestamp"] == 10.0
    assert "scene_order_suggestion" in intel["hook"]
    
    # 2. 5 YouTube Shorts Titles
    assert len(intel["titles"]) == 5
    for t in intel["titles"]:
        assert len(t) > 0 and len(t) <= 100
        
    # 3. Description
    assert "Recomposition" in intel["description"] or "optimize" in intel["description"]
    assert "#Shorts" in intel["description"]
    
    # 4. Hashtags
    assert len(intel["hashtags"]) >= 4
    for tag in intel["hashtags"]:
        assert tag.startswith("#")
        
    # 5. Scene Intelligence
    assert "strongest_scene" in intel["scene_intelligence"]
    assert "retention_suggestions" in intel["scene_intelligence"]
    assert "weak_sections" in intel["scene_intelligence"]
    assert "editing_improvements" in intel["scene_intelligence"]

    # 6. Opus Virality Score & 4 Retention Pillars
    assert "virality_score" in intel
    assert 0 <= intel["virality_score"] <= 100
    assert intel["virality_grade"] in {"A+", "A", "B", "C"}
    assert "flow_score" in intel
    assert "engagement_score" in intel
    assert "trend_score" in intel
    assert "why_viral" in intel
    assert isinstance(intel["improvement_tips"], list)
    assert len(intel["improvement_tips"]) >= 1


def test_generate_opus_ass(tmp_path):
    from autoclip_app.pipeline import Segment, generate_opus_ass

    words = (
        {"word": "This", "start": 0.0, "end": 0.3},
        {"word": "is", "start": 0.3, "end": 0.5},
        {"word": "insane", "start": 0.5, "end": 1.0},
        {"word": "money", "start": 1.0, "end": 1.4},
    )
    rows = [Segment(0.0, 1.4, "This is insane money", words)]
    ass_path = tmp_path / "test_opus.ass"
    
    generate_opus_ass(
        rows=rows,
        clip_start=0.0,
        clip_end=1.4,
        target_ass=ass_path,
        caption_style="KINETIC_YELLOW",
        hook_title="BIG WIN ALERT"
    )

    assert ass_path.exists()
    content = ass_path.read_text(encoding="utf-8")
    assert "[Script Info]" in content
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "BIG WIN ALERT" in content
    # Emoji injection for "money"
    assert "💰" in content or "insane" in content


def test_render_opus_fit_blur(tmp_path):
    import subprocess
    from autoclip_app.pipeline import Candidate, Segment, render

    synth_video = tmp_path / "src_opus.mp4"
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-f", "lavfi", "-i", "color=c=purple:s=640x360:d=2,format=yuv420p",
        "-f", "lavfi", "-i", "sine=frequency=500:duration=2",
        "-c:v", "libx264", "-c:a", "aac", str(synth_video)
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    words = (
        {"word": "Opus", "start": 0.1, "end": 0.5},
        {"word": "style", "start": 0.5, "end": 0.9},
    )
    rows = [Segment(0.0, 1.0, "Opus style", words)]
    clip = Candidate(0.0, 1.0, "Opus Test Clip", "Viral clip test", 92, "Opus style")

    target_video = tmp_path / "opus_output.mp4"
    render(
        source=synth_video,
        rows=rows,
        clip=clip,
        target=target_video,
        layout="FIT_BLUR",
        caption_style="NEON_MINT",
        show_hook_header=True
    )

    assert target_video.exists()
    assert target_video.stat().st_size > 0
    assert target_video.with_suffix(".srt").exists()
    assert target_video.with_suffix(".ass").exists()


def test_segment_supports_word_timestamps():
    words = (
        {"word": "Hello", "start": 0.0, "end": 0.5},
        {"word": "world", "start": 0.6, "end": 1.0},
    )
    seg = Segment(0.0, 1.0, "Hello world", words)
    assert len(seg.words) == 2
    assert seg.words[0]["word"] == "Hello"


def test_format_chapter_timestamp():
    from autoclip_app.pipeline import _format_chapter_timestamp
    assert _format_chapter_timestamp(0) == "00:00"
    assert _format_chapter_timestamp(75) == "01:15"
    assert _format_chapter_timestamp(3672) == "01:01:12"


def test_generate_long_form_metadata(monkeypatch):
    from autoclip_app.pipeline import Candidate, generate_long_form_metadata

    def unavailable(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr("autoclip_app.pipeline.requests.post", unavailable)
    candidates = [
        Candidate(0.0, 30.0, "Opening Strategy", "Explains early game setup", 90, "Early game strategy is critical."),
        Candidate(35.0, 70.0, "Mid-game Execution", "Covers the team rotation", 85, "Rotate quickly to secure objective.")
    ]
    chapters = [
        {"index": 1, "start_seconds": 0.0, "timestamp": "00:00", "title": "Opening Strategy", "duration": 30.0},
        {"index": 2, "start_seconds": 30.0, "timestamp": "00:30", "title": "Mid-game Execution", "duration": 35.0}
    ]
    meta = generate_long_form_metadata(candidates, chapters, source_title="Valorant Championship", creator_name="ProPlayer")
    assert "Valorant Championship" in meta["title"]
    assert "00:00 Opening Strategy" in meta["description"]
    assert "ProPlayer" in meta["description"]
    assert len(meta["tags"]) >= 5


def test_render_long_form_compilation(tmp_path):
    import subprocess
    from autoclip_app.pipeline import Candidate, Segment, render_long_form

    # 1. Create a 4-second synthetic mp4 with audio
    synth_video = tmp_path / "source.mp4"
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-f", "lavfi", "-i", "color=c=navy:s=640x360:d=4,format=yuv420p",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-c:v", "libx264", "-c:a", "aac", str(synth_video)
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    # 2. Segments and Candidates
    rows = [
        Segment(0.2, 1.2, "Welcome to the game.", ()),
        Segment(2.1, 3.2, "What an incredible move.", ()),
    ]
    candidates = [
        Candidate(0.0, 1.5, "Part 1: Welcome", "Initial play", 90, "Welcome to the game."),
        Candidate(2.0, 3.5, "Part 2: Highlight", "Clutch moment", 95, "What an incredible move."),
    ]

    target_video = tmp_path / "compilation.mp4"
    result = render_long_form(
        source=synth_video,
        rows=rows,
        candidates=candidates,
        target=target_video,
        title="Epic Highlights",
        creator_name="GamerAlpha"
    )

    assert target_video.exists()
    assert target_video.stat().st_size > 0
    assert result["format"] == "16:9"
    assert result["duration"] == 3.0  # 1.5s + 1.5s = 3.0s
    assert len(result["chapters"]) == 2
    assert "00:00 Part 1: Welcome" in result["chapters_text"]
    assert "00:02 Part 2: Highlight" in result["chapters_text"]
    assert len(result["youtube_title"]) > 0
    assert target_video.with_suffix(".srt").exists()

