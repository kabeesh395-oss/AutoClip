package com.autoclip.app

import com.autoclip.app.data.models.*
import com.google.gson.Gson
import org.junit.Assert.*
import org.junit.Test

class ModelsTest {

    private val gson = Gson()

    @Test
    fun testServerConfigBaseUrl() {
        val config = ServerConfig(host = "192.168.1.50", port = 8765)
        assertEquals("http://192.168.1.50:8765", config.baseUrl)
    }

    @Test
    fun testClipDtoFullUrls() {
        val clip = ClipDto(
            id = 1,
            title = "Test Clip",
            videoUrl = "/outputs/job123/01-clip.mp4",
            subtitleUrl = "/outputs/job123/01-clip.srt"
        )
        val baseUrl = "http://192.168.1.50:8765"
        assertEquals("http://192.168.1.50:8765/outputs/job123/01-clip.mp4", clip.fullVideoUrl(baseUrl))
        assertEquals("http://192.168.1.50:8765/outputs/job123/01-clip.srt", clip.fullSubtitleUrl(baseUrl))
    }

    @Test
    fun testClipDtoSerializationWithViralIntelligence() {
        val json = """
            {
              "id": 1,
              "title": "Groundbreaking AI Tip",
              "reason": "Clear explanation",
              "score": 94.5,
              "start": 10.0,
              "end": 35.0,
              "duration": 25.0,
              "titles": [
                "Title 1: Why AI Matters",
                "Title 2: The Secret of AI",
                "Title 3",
                "Title 4",
                "Title 5"
              ],
              "description": "Engaging description\n\n#Shorts",
              "hashtags": ["#Shorts", "#Viral", "#AI"],
              "hook": {
                "opening_hook": "Watch this before using AI again",
                "hook_timestamp": 10.5,
                "hook_type": "Curiosity",
                "detected_pauses": "None",
                "scene_order_suggestion": "Start at 0:10"
              },
              "scene_intelligence": {
                "strongest_scene": "Middle climax",
                "retention_suggestions": "Add zoom",
                "weak_sections": "None",
                "editing_improvements": "Add music hit"
              },
              "subtitles_srt": "1\n00:00:10,000 --> 00:00:35,000\nSubtitles text",
              "words": [
                {"word": "Watch", "start": 10.0, "end": 10.4},
                {"word": "this", "start": 10.5, "end": 10.8}
              ],
              "video_url": "/outputs/j1/01.mp4",
              "subtitle_url": "/outputs/j1/01.srt"
            }
        """.trimIndent()

        val clip = gson.fromJson(json, ClipDto::class.java)
        assertEquals(1, clip.id)
        assertEquals("Title 1: Why AI Matters", clip.displayTitle())
        assertEquals(5, clip.titles.size)
        assertEquals(3, clip.hashtags.size)
        assertNotNull(clip.hook)
        assertEquals("Watch this before using AI again", clip.hook?.openingHook)
        assertEquals(10.5, clip.hook?.hookTimestamp ?: 0.0, 0.01)
        assertNotNull(clip.sceneIntelligence)
        assertEquals("Middle climax", clip.sceneIntelligence?.strongestScene)
        assertEquals(2, clip.words.size)
        assertEquals("Watch", clip.words[0].word)
    }

    @Test
    fun testJobDtoDeserialization() {
        val json = """
            {
              "id": "job-abc-123",
              "status": "completed",
              "message": "All clips ready",
              "source": "https://www.youtube.com/watch?v=example",
              "clips": []
            }
        """.trimIndent()

        val job = gson.fromJson(json, JobDto::class.java)
        assertEquals("job-abc-123", job.id)
        assertEquals("completed", job.status)
        assertEquals("All clips ready", job.message)
    }
}
