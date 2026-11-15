package com.autoclip.app.ui.studio

import android.content.Context
import android.widget.Toast
import androidx.annotation.OptIn
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import com.autoclip.app.data.ApiClient
import com.autoclip.app.data.DownloadHelper
import com.autoclip.app.data.PreferencesManager
import com.autoclip.app.data.models.ClipDto
import com.autoclip.app.data.models.JobDto
import com.autoclip.app.ui.components.CopyButton
import com.autoclip.app.ui.components.ScoreBadge
import com.autoclip.app.ui.theme.*
import kotlinx.coroutines.launch

@kotlin.OptIn(UnstableApi::class, ExperimentalMaterial3Api::class)
@Composable
fun ClipStudioScreen(
    jobId: String,
    initialClipIndex: Int = 0,
    onNavigateBack: () -> Unit
) {
    val context = LocalContext.current
    val prefs = remember { PreferencesManager(context) }
    val apiClient = remember { ApiClient() }
    val scope = rememberCoroutineScope()

    var job by remember { mutableStateOf<JobDto?>(null) }
    var selectedIndex by remember { mutableIntStateOf(initialClipIndex) }
    var isRegenerating by remember { mutableStateOf(false) }

    // ExoPlayer state
    val exoPlayer = remember {
        ExoPlayer.Builder(context).build().apply {
            playWhenReady = false
        }
    }

    DisposableEffect(Unit) {
        onDispose {
            exoPlayer.release()
        }
    }

    fun loadJob() {
        val config = prefs.getServerConfig()
        scope.launch {
            val res = apiClient.getJob(config.baseUrl, jobId)
            if (res.isSuccess) {
                job = res.getOrNull()
            }
        }
    }

    LaunchedEffect(jobId) {
        loadJob()
    }

    val clips = job?.clips ?: emptyList()
    val currentClip = clips.getOrNull(selectedIndex)
    val config = remember { prefs.getServerConfig() }

    LaunchedEffect(currentClip?.videoUrl) {
        currentClip?.let {
            val fullUrl = it.fullVideoUrl(config.baseUrl)
            exoPlayer.setMediaItem(MediaItem.fromUri(fullUrl))
            exoPlayer.prepare()
        }
    }

    fun regenerateMetadata(clipId: Int) {
        isRegenerating = true
        scope.launch {
            val res = apiClient.regenerateMetadata(config.baseUrl, jobId, clipId)
            isRegenerating = false
            if (res.isSuccess) {
                Toast.makeText(context, "AI Hook and Metadata regenerated", Toast.LENGTH_SHORT).show()
                loadJob()
            } else {
                Toast.makeText(context, "Regeneration failed: ${res.exceptionOrNull()?.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(
                            text = "✂ Viral Shorts Studio",
                            style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                            color = BrandTextPrimary
                        )
                        Text(
                            text = "Job #$jobId · Clip ${selectedIndex + 1}/${clips.size.coerceAtLeast(1)}",
                            style = MaterialTheme.typography.labelSmall,
                            color = BrandTextSecondary
                        )
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onNavigateBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back", tint = BrandTextPrimary)
                    }
                },
                actions = {
                    currentClip?.let { clip ->
                        IconButton(onClick = { regenerateMetadata(clip.id) }, enabled = !isRegenerating) {
                            if (isRegenerating) {
                                CircularProgressIndicator(modifier = Modifier.size(20.dp), color = BrandMintAccent, strokeWidth = 2.dp)
                            } else {
                                Icon(Icons.Default.AutoAwesome, contentDescription = "Regenerate", tint = BrandMintAccent)
                            }
                        }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = BrandNavyBg)
            )
        },
        containerColor = BrandNavyBg
    ) { padding ->
        if (currentClip == null) {
            Box(modifier = Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = BrandMintAccent)
            }
        } else {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                // Clip switcher tabs
                if (clips.size > 1) {
                    ScrollableTabRow(
                        selectedTabIndex = selectedIndex,
                        containerColor = BrandCardSurface,
                        contentColor = BrandMintAccent,
                        edgePadding = 16.dp,
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        clips.forEachIndexed { idx, c ->
                            Tab(
                                selected = selectedIndex == idx,
                                onClick = { selectedIndex = idx },
                                text = {
                                    Text(
                                        text = "Clip ${idx + 1} (${c.score.toInt()}%)",
                                        fontWeight = if (selectedIndex == idx) FontWeight.Bold else FontWeight.Normal
                                    )
                                }
                            )
                        }
                    }
                }

                // Video Player Container (9:16 Aspect Ratio)
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp)
                        .height(340.dp)
                        .clip(RoundedCornerShape(16.dp))
                        .background(Color.Black)
                        .border(1.dp, BrandBorder, RoundedCornerShape(16.dp)),
                    contentAlignment = Alignment.Center
                ) {
                    AndroidView(
                        factory = { ctx ->
                            PlayerView(ctx).apply {
                                player = exoPlayer
                                useController = true
                            }
                        },
                        modifier = Modifier.fillMaxSize()
                    )
                }

                // Download & Seek Actions Bar
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp),
                    horizontalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Button(
                        onClick = {
                            DownloadHelper.downloadClipViaManager(
                                context = context,
                                url = currentClip.fullVideoUrl(config.baseUrl),
                                title = currentClip.title,
                                extension = "mp4"
                            )
                        },
                        shape = RoundedCornerShape(12.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = BrandMintAccent, contentColor = Color(0xFF041913)),
                        modifier = Modifier.weight(1f)
                    ) {
                        Icon(Icons.Default.Download, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(modifier = Modifier.width(6.dp))
                        Text("Download MP4", fontWeight = FontWeight.Bold)
                    }

                    OutlinedButton(
                        onClick = {
                            if (currentClip.subtitlesSrt.isNotBlank()) {
                                scope.launch {
                                    val file = DownloadHelper.saveSrtLocally(context, currentClip.title, currentClip.subtitlesSrt)
                                    Toast.makeText(context, "Saved SRT to Downloads: ${file?.name}", Toast.LENGTH_SHORT).show()
                                }
                            } else {
                                Toast.makeText(context, "No subtitle content found", Toast.LENGTH_SHORT).show()
                            }
                        },
                        shape = RoundedCornerShape(12.dp),
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = BrandSkyAccent),
                        border = androidx.compose.foundation.BorderStroke(1.dp, BrandSkyAccent),
                        modifier = Modifier.weight(1f)
                    ) {
                        Icon(Icons.Default.Subtitles, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(modifier = Modifier.width(6.dp))
                        Text("Download SRT", fontWeight = FontWeight.Bold)
                    }
                }

                // 1. AI HOOK GENERATOR SECTION
                val hook = currentClip.hook
                Card(
                    shape = RoundedCornerShape(16.dp),
                    colors = CardDefaults.cardColors(containerColor = BrandCardSurface),
                    border = androidx.compose.foundation.BorderStroke(1.dp, BrandMintAccent.copy(alpha = 0.5f)),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)
                ) {
                    Column(modifier = Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Icon(Icons.Default.AutoAwesome, contentDescription = null, tint = BrandMintAccent)
                                Text(
                                    text = "AI Viral Opening Hook",
                                    style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                                    color = BrandTextPrimary
                                )
                            }
                            ScoreBadge(currentClip.score)
                        }

                        val hookText = hook?.openingHook?.ifBlank { currentClip.title } ?: currentClip.title
                        Text(
                            text = "\"$hookText\"",
                            style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.Bold),
                            color = BrandMintAccent
                        )

                        // Hook timestamp and seek action
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                text = "Best opening timestamp: ${hook?.hookTimestamp ?: currentClip.start}s",
                                style = MaterialTheme.typography.bodySmall,
                                color = BrandTextSecondary
                            )

                            FilledTonalButton(
                                onClick = {
                                    val offsetSec = ((hook?.hookTimestamp ?: currentClip.start) - currentClip.start).coerceAtLeast(0.0)
                                    exoPlayer.seekTo((offsetSec * 1000).toLong())
                                    exoPlayer.play()
                                },
                                shape = RoundedCornerShape(8.dp),
                                colors = ButtonDefaults.filledTonalButtonColors(containerColor = BrandCardElevated, contentColor = BrandMintAccent)
                            ) {
                                Icon(Icons.Default.PlayArrow, contentDescription = null, modifier = Modifier.size(16.dp))
                                Spacer(modifier = Modifier.width(4.dp))
                                Text("Seek to Hook", fontSize = 12.sp)
                            }
                        }

                        hook?.let {
                            if (it.detectedPauses.isNotBlank()) {
                                Text(
                                    text = "⚡ Pause / Pacing: ${it.detectedPauses}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = BrandWarning
                                )
                            }
                            if (it.sceneOrderSuggestion.isNotBlank()) {
                                Text(
                                    text = "🎬 Scene Sequence: ${it.sceneOrderSuggestion}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = BrandSkyAccent
                                )
                            }
                        }

                        CopyButton(textToCopy = hookText, label = "Copy AI Hook", modifier = Modifier.align(Alignment.End))
                    }
                }

                // 2. YOUTUBE SHORTS TITLES (5 OPTIONS)
                val titles = if (currentClip.titles.isNotEmpty()) currentClip.titles else listOf(currentClip.displayTitle())
                Card(
                    shape = RoundedCornerShape(16.dp),
                    colors = CardDefaults.cardColors(containerColor = BrandCardSurface),
                    border = androidx.compose.foundation.BorderStroke(1.dp, BrandBorder),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)
                ) {
                    Column(modifier = Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Icon(Icons.Default.Title, contentDescription = null, tint = BrandSkyAccent)
                            Text(
                                text = "5 Viral Shorts Title Options",
                                style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                                color = BrandTextPrimary
                            )
                        }

                        titles.forEachIndexed { i, t ->
                            Card(
                                shape = RoundedCornerShape(10.dp),
                                colors = CardDefaults.cardColors(containerColor = BrandCardElevated),
                                border = androidx.compose.foundation.BorderStroke(1.dp, BrandBorder),
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                Row(
                                    modifier = Modifier.padding(12.dp).fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Column(modifier = Modifier.weight(1f).padding(end = 8.dp)) {
                                        Text(
                                            text = "Option ${i + 1}",
                                            style = MaterialTheme.typography.labelSmall,
                                            color = BrandTextMuted
                                        )
                                        Text(
                                            text = t,
                                            style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold),
                                            color = BrandTextPrimary
                                        )
                                    }
                                    CopyButton(textToCopy = t, label = "Copy")
                                }
                            }
                        }
                    }
                }

                // 3. CAPTIONS & SUBTITLES SECTION
                var isEditingSrt by remember { mutableStateOf(false) }
                var editableSrt by remember { mutableStateOf(currentClip.subtitlesSrt) }

                Card(
                    shape = RoundedCornerShape(16.dp),
                    colors = CardDefaults.cardColors(containerColor = BrandCardSurface),
                    border = androidx.compose.foundation.BorderStroke(1.dp, BrandBorder),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)
                ) {
                    Column(modifier = Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Icon(Icons.Default.Subtitles, contentDescription = null, tint = BrandMintAccent)
                                Text(
                                    text = "Captions & Subtitles",
                                    style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                                    color = BrandTextPrimary
                                )
                            }
                            IconButton(onClick = { isEditingSrt = !isEditingSrt }) {
                                Icon(if (isEditingSrt) Icons.Default.Check else Icons.Default.Edit, contentDescription = "Edit", tint = BrandMintAccent)
                            }
                        }

                        if (isEditingSrt) {
                            OutlinedTextField(
                                value = editableSrt,
                                onValueChange = { editableSrt = it },
                                label = { Text("Edit SRT Subtitles") },
                                modifier = Modifier.fillMaxWidth().heightIn(min = 120.dp, max = 240.dp),
                                colors = OutlinedTextFieldDefaults.colors(
                                    focusedTextColor = BrandTextPrimary,
                                    unfocusedTextColor = BrandTextPrimary,
                                    focusedBorderColor = BrandMintAccent,
                                    unfocusedBorderColor = BrandBorder
                                )
                            )
                            Button(
                                onClick = {
                                    scope.launch {
                                        apiClient.updateClip(config.baseUrl, jobId, currentClip.id, mapOf("subtitles_srt" to editableSrt))
                                        isEditingSrt = false
                                        Toast.makeText(context, "Subtitles updated on PC", Toast.LENGTH_SHORT).show()
                                        loadJob()
                                    }
                                },
                                shape = RoundedCornerShape(10.dp),
                                colors = ButtonDefaults.buttonColors(containerColor = BrandMintAccent, contentColor = Color(0xFF041913))
                            ) {
                                Text("Save Subtitles")
                            }
                        } else {
                            Surface(
                                shape = RoundedCornerShape(10.dp),
                                color = BrandCardElevated,
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                Text(
                                    text = currentClip.subtitlesSrt.ifBlank { "Synchronized burned-in subtitles rendered in MP4." },
                                    style = MaterialTheme.typography.bodySmall,
                                    color = BrandTextSecondary,
                                    modifier = Modifier.padding(12.dp),
                                    maxLines = 6,
                                    overflow = TextOverflow.Ellipsis
                                )
                            }
                            CopyButton(textToCopy = currentClip.subtitlesSrt, label = "Copy Full SRT", modifier = Modifier.align(Alignment.End))
                        }
                    }
                }

                // 4. DESCRIPTION & CALL TO ACTION
                val desc = currentClip.displayDescription()
                Card(
                    shape = RoundedCornerShape(16.dp),
                    colors = CardDefaults.cardColors(containerColor = BrandCardSurface),
                    border = androidx.compose.foundation.BorderStroke(1.dp, BrandBorder),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)
                ) {
                    Column(modifier = Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Icon(Icons.Default.Description, contentDescription = null, tint = BrandSkyAccent)
                            Text(
                                text = "SEO Description & CTA",
                                style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                                color = BrandTextPrimary
                            )
                        }

                        Surface(
                            shape = RoundedCornerShape(10.dp),
                            color = BrandCardElevated,
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text(
                                text = desc,
                                style = MaterialTheme.typography.bodySmall,
                                color = BrandTextPrimary,
                                modifier = Modifier.padding(12.dp)
                            )
                        }

                        CopyButton(textToCopy = desc, label = "Copy Description", modifier = Modifier.align(Alignment.End))
                    }
                }

                // 5. HASHTAGS GENERATOR
                val tags = if (currentClip.hashtags.isNotEmpty()) currentClip.hashtags else currentClip.youtubeTags.map { "#$it" }
                Card(
                    shape = RoundedCornerShape(16.dp),
                    colors = CardDefaults.cardColors(containerColor = BrandCardSurface),
                    border = androidx.compose.foundation.BorderStroke(1.dp, BrandBorder),
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)
                ) {
                    Column(modifier = Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Icon(Icons.Default.Tag, contentDescription = null, tint = BrandMintAccent)
                            Text(
                                text = "Viral Hashtags",
                                style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                                color = BrandTextPrimary
                            )
                        }

                        // Tags display
                        Text(
                            text = tags.joinToString(" "),
                            style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold),
                            color = BrandMintAccent
                        )

                        CopyButton(
                            textToCopy = tags.joinToString(" "),
                            label = "Copy All Hashtags",
                            modifier = Modifier.align(Alignment.End)
                        )
                    }
                }

                // 6. SCENE INTELLIGENCE
                currentClip.sceneIntelligence?.let { intel ->
                    Card(
                        shape = RoundedCornerShape(16.dp),
                        colors = CardDefaults.cardColors(containerColor = BrandCardSurface),
                        border = androidx.compose.foundation.BorderStroke(1.dp, BrandBorder),
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)
                    ) {
                        Column(modifier = Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Icon(Icons.Default.Analytics, contentDescription = null, tint = BrandSkyAccent)
                                Text(
                                    text = "Scene Intelligence & Retention",
                                    style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                                    color = BrandTextPrimary
                                )
                            }

                            if (intel.strongestScene.isNotBlank()) {
                                Text("🎯 Strongest Scene", style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold), color = BrandMintAccent)
                                Text(intel.strongestScene, style = MaterialTheme.typography.bodySmall, color = BrandTextPrimary)
                            }

                            if (intel.retentionSuggestions.isNotBlank()) {
                                Text("📈 3-Second Retention Tips", style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold), color = BrandWarning)
                                Text(intel.retentionSuggestions, style = MaterialTheme.typography.bodySmall, color = BrandTextPrimary)
                            }

                            if (intel.weakSections.isNotBlank()) {
                                Text("✂ Sections to Tighten", style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold), color = BrandError)
                                Text(intel.weakSections, style = MaterialTheme.typography.bodySmall, color = BrandTextPrimary)
                            }

                            if (intel.editingImprovements.isNotBlank()) {
                                Text("💡 Editing Improvements", style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold), color = BrandSkyAccent)
                                Text(intel.editingImprovements, style = MaterialTheme.typography.bodySmall, color = BrandTextPrimary)
                            }
                        }
                    }
                }

                Spacer(modifier = Modifier.height(24.dp))
            }
        }
    }
}
