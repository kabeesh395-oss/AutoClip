/**
 * AutoClip Cloud Control Plane - Cloudflare Worker
 * Handles API requests, Job Queues in D1, and R2 asset serving.
 * 100% Free Tier compatible.
 */

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Worker-Key",
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
      ...CORS_HEADERS,
    },
  });
}

function err(message, status = 400) {
  return json({ error: message, detail: message }, status);
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS_HEADERS });
    }

    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;
    const db = env.DB;
    const r2 = env.BUCKET;

    try {
      // 1. Health & Server Info
      if (path === "/api/health" && method === "GET") {
        return json({
          status: "ok",
          app: "AutoClip Cloud Control Plane",
          mode: "cloud-control-local-render",
          timezone: env.TIMEZONE || "Asia/Kolkata",
          timestamp: new Date().toISOString(),
        });
      }

      if (path === "/api/server-info" && method === "GET") {
        return json({
          app_name: "AutoClip AI",
          version: "2.0.0-cloud",
          timezone: "Asia/Kolkata",
          default_batch_config: {
            videos_per_day: 3,
            schedule_days: 3,
            batch_target: 9,
            daily_shorts: 2,
            daily_long: 1,
            manual_approval_required: true,
            slots: ["12:00 PM (Short)", "04:00 PM (Short)", "07:00 PM (Long)"],
          },
        });
      }

      // 2. Batches Endpoints
      if (path === "/api/batches" && method === "GET") {
        const { results } = await db.prepare("SELECT * FROM batches ORDER BY created_at DESC LIMIT 50").all();
        return json({ batches: results || [] });
      }

      if (path === "/api/batches" && method === "POST") {
        const body = await request.json();
        const id = "batch_" + crypto.randomUUID().slice(0, 8);
        const name = String(body.name || "Untitled Batch").trim();
        const now = new Date().toISOString();

        await db.prepare(`
          INSERT INTO batches (
            id, name, status, target_videos_per_day, schedule_days,
            batch_target, daily_shorts, daily_long, timezone, created_at, updated_at
          ) VALUES (?, ?, 'DRAFT', 3, 3, 9, 2, 1, 'Asia/Kolkata', ?, ?)
        `).bind(id, name, now, now).run();

        return json({ id, name, status: "DRAFT", batch_target: 9, created_at: now }, 201);
      }

      // 3. Single Batch Details
      const batchMatch = path.match(/^\/api\/batches\/([a-zA-Z0-9_-]+)$/);
      if (batchMatch && method === "GET") {
        const batchId = batchMatch[1];
        const batch = await db.prepare("SELECT * FROM batches WHERE id = ?").bind(batchId).first();
        if (!batch) return err("Batch not found", 404);

        const { results: sources } = await db.prepare("SELECT * FROM sources WHERE batch_id = ?").bind(batchId).all();
        const { results: jobs } = await db.prepare("SELECT * FROM jobs WHERE batch_id = ?").bind(batchId).all();
        const { results: clips } = await db.prepare("SELECT * FROM clips WHERE batch_id = ?").bind(batchId).all();

        return json({
          ...batch,
          sources: sources || [],
          jobs: jobs || [],
          clips: clips || [],
        });
      }

      // 4. Add Source to Batch
      const addSourceMatch = path.match(/^\/api\/batches\/([a-zA-Z0-9_-]+)\/sources$/);
      if (addSourceMatch && method === "POST") {
        const batchId = addSourceMatch[1];
        const body = await request.json();
        const sourceUrl = String(body.source_url || "").trim();
        if (!sourceUrl) return err("source_url is required");

        const sourceId = "src_" + crypto.randomUUID().slice(0, 8);
        const now = new Date().toISOString();
        const title = String(body.title || "Video Source").trim();
        const game = String(body.game_name || "General").trim();

        await db.prepare(`
          INSERT INTO sources (id, batch_id, source_url, title, game_name, rights_status, created_at)
          VALUES (?, ?, ?, ?, ?, 'PASS', ?)
        `).bind(sourceId, batchId, sourceUrl, title, game, now).run();

        return json({ id: sourceId, batch_id: batchId, source_url: sourceUrl, title }, 201);
      }

      // 5. Start Batch Processing (Queue Jobs for Local Worker)
      const startMatch = path.match(/^\/api\/batches\/([a-zA-Z0-9_-]+)\/start$/);
      if (startMatch && method === "POST") {
        const batchId = startMatch[1];
        const { results: sources } = await db.prepare("SELECT * FROM sources WHERE batch_id = ?").bind(batchId).all();
        if (!sources || sources.length === 0) return err("Add at least one source before starting");

        const now = new Date().toISOString();
        const createdJobs = [];

        for (const src of sources) {
          const jobId = "job_" + crypto.randomUUID().slice(0, 8);
          await db.prepare(`
            INSERT INTO jobs (id, batch_id, source_id, status, progress, stage_message, updated_at)
            VALUES (?, ?, ?, 'QUEUED', 0, 'Queued for Local PC Worker', ?)
          `).bind(jobId, batchId, src.id, now).run();

          createdJobs.push(jobId);
        }

        await db.prepare("UPDATE batches SET status = 'PROCESSING', updated_at = ? WHERE id = ?").bind(now, batchId).run();

        return json({
          status: "PROCESSING",
          queued_jobs: createdJobs,
          message: "Jobs queued. Local PC Worker will render and upload to R2.",
        });
      }

      // 6. Get Batch Clips & Virality Data
      const clipsMatch = path.match(/^\/api\/batches\/([a-zA-Z0-9_-]+)\/clips$/);
      if (clipsMatch && method === "GET") {
        const batchId = clipsMatch[1];
        const { results: rawClips } = await db.prepare("SELECT * FROM clips WHERE batch_id = ? ORDER BY created_at DESC").bind(batchId).all();

        const clips = (rawClips || []).map((c) => {
          let titles = [];
          try { titles = JSON.parse(c.titles_json || "[]"); } catch { titles = [c.title]; }
          let hook = {};
          try { hook = JSON.parse(c.hook_json || "{}"); } catch { hook = {}; }
          let tips = [];
          try { tips = JSON.parse(c.improvement_tips_json || "[]"); } catch { tips = []; }

          return {
            ...c,
            titles,
            hook,
            improvement_tips: tips,
            video_url: c.r2_video_key ? `/storage/${c.r2_video_key}` : (c.output_path || ""),
            subtitle_url: c.r2_subtitle_key ? `/storage/${c.r2_subtitle_key}` : "",
            ass_url: c.r2_ass_key ? `/storage/${c.r2_ass_key}` : "",
          };
        });

        return json({ clips, count: clips.length });
      }

      // 7. Manual Approval Gate (100% User Control)
      const approvalMatch = path.match(/^\/api\/clips\/([a-zA-Z0-9_-]+)\/approval$/);
      if (approvalMatch && method === "PUT") {
        const clipId = approvalMatch[1];
        const body = await request.json();
        const status = String(body.approval_status || "PENDING").toUpperCase();
        if (!["APPROVED", "REJECTED", "PENDING"].includes(status)) {
          return err("Invalid status. Allowed: APPROVED, REJECTED, PENDING");
        }

        const now = new Date().toISOString();
        await db.prepare(`
          UPDATE clips SET approval_status = ?, approved_at = ? WHERE id = ?
        `).bind(status, status === "APPROVED" ? now : null, clipId).run();

        return json({ clip_id: clipId, approval_status: status });
      }

      // 8. Local Worker Polling Endpoint (PC Worker -> Cloudflare)
      if (path === "/api/worker/poll" && method === "POST") {
        const body = await request.json().catch(() => ({}));
        const workerId = String(body.worker_id || "pc-local-worker");

        // Find the oldest QUEUED job
        const job = await db.prepare(`
          SELECT j.*, s.source_url, s.title as source_title, b.language, b.whisper_model, b.daily_shorts, b.daily_long
          FROM jobs j
          JOIN sources s ON j.source_id = s.id
          JOIN batches b ON j.batch_id = b.id
          WHERE j.status = 'QUEUED'
          ORDER BY j.updated_at ASC
          LIMIT 1
        `).first();

        if (!job) {
          return json({ has_job: false });
        }

        const now = new Date().toISOString();
        await db.prepare(`
          UPDATE jobs SET status = 'PROCESSING', stage_message = 'Rendering on PC Worker', worker_id = ?, updated_at = ?
          WHERE id = ?
        `).bind(workerId, now, job.id).run();

        return json({
          has_job: true,
          job: {
            id: job.id,
            batch_id: job.batch_id,
            source_id: job.source_id,
            source_url: job.source_url,
            language: job.language || "en",
            whisper_model: job.whisper_model || "small",
            daily_shorts: job.daily_shorts || 2,
            daily_long: job.daily_long || 1,
          },
        });
      }

      // 9. Local Worker Reports Completed Job & Uploaded R2 Asset Keys
      const completeMatch = path.match(/^\/api\/worker\/jobs\/([a-zA-Z0-9_-]+)\/complete$/);
      if (completeMatch && method === "POST") {
        const jobId = completeMatch[1];
        const body = await request.json();
        const now = new Date().toISOString();

        const clips = body.clips || [];
        for (const c of clips) {
          const clipId = "clip_" + crypto.randomUUID().slice(0, 8);
          await db.prepare(`
            INSERT INTO clips (
              id, batch_id, source_id, format, start_time, end_time, duration,
              hook_score, virality_grade, flow_score, engagement_score, trend_score,
              layout, caption_style, why_viral, improvement_tips_json,
              title, titles_json, description, hashtags_json, hook_json,
              r2_video_key, r2_subtitle_key, r2_ass_key,
              approval_status, created_at
            ) VALUES (
              ?, ?, ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?,
              ?, ?, ?, ?,
              ?, ?, ?, ?, ?,
              ?, ?, ?,
              'PENDING', ?
            )
          `).bind(
            clipId, c.batch_id, c.source_id, c.format || "SHORT", c.start_time || 0, c.end_time || 15, c.duration || 15,
            c.hook_score || 85, c.virality_grade || "A", c.flow_score || 80, c.engagement_score || 85, c.trend_score || 80,
            c.layout || "FIT_BLUR", c.caption_style || "KINETIC_YELLOW", c.why_viral || "", JSON.stringify(c.improvement_tips || []),
            c.title || "Viral Clip", JSON.stringify(c.titles || [c.title]), c.description || "", JSON.stringify(c.hashtags || []), JSON.stringify(c.hook || {}),
            c.r2_video_key || "", c.r2_subtitle_key || "", c.r2_ass_key || "",
            now
          ).run();
        }

        await db.prepare(`
          UPDATE jobs SET status = 'COMPLETED', progress = 100, stage_message = 'Rendered and uploaded to R2', completed_at = ?, updated_at = ?
          WHERE id = ?
        `).bind(now, now, jobId).run();

        return json({ status: "COMPLETED", job_id: jobId, clips_created: clips.length });
      }

      // 10. Serve R2 Storage Files directly (Videos, Subtitles, Thumbnails)
      const storageMatch = path.match(/^\/storage\/(.+)$/);
      if (storageMatch && method === "GET") {
        if (!r2) return err("R2 storage binding not configured", 500);
        const objectKey = decodeURIComponent(storageMatch[1]);
        const object = await r2.get(objectKey);
        if (!object) return err("Asset not found in R2 storage", 404);

        const headers = new Headers(CORS_HEADERS);
        object.writeHttpMetadata(headers);
        headers.set("etag", object.httpEtag);
        headers.set("Cache-Control", "public, max-age=31536000, immutable");

        return new Response(object.body, { headers });
      }

      return err(`Route not found: ${method} ${path}`, 404);
    } catch (e) {
      return json({ error: "Internal Server Error", message: e.message, stack: e.stack }, 500);
    }
  },
};
