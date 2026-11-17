package com.autoclip.app.ui.progress

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.autoclip.app.data.ApiClient
import com.autoclip.app.data.PreferencesManager
import com.autoclip.app.data.models.JobDto
import com.autoclip.app.ui.components.AutoClipTopBar
import com.autoclip.app.ui.components.StatusBadge
import com.autoclip.app.ui.theme.*
import kotlinx.coroutines.delay

@Composable
fun JobProgressScreen(
    jobId: String,
    onNavigateBack: () -> Unit,
    onOpenStudio: (String, Int) -> Unit
) {
    val context = LocalContext.current
    val prefs = remember { PreferencesManager(context) }
    val apiClient = remember { ApiClient() }

    var job by remember { mutableStateOf<JobDto?>(null) }
    var errorMsg by remember { mutableStateOf<String?>(null) }
    var isPolling by remember { mutableStateOf(true) }

    LaunchedEffect(jobId) {
        val config = prefs.getServerConfig()
        while (isPolling) {
            val res = apiClient.getJob(config.baseUrl, jobId)
            if (res.isSuccess) {
                val currentJob = res.getOrNull()!!
                job = currentJob
                prefs.saveJob(currentJob)
                if (currentJob.status == "completed" || currentJob.status == "failed") {
                    isPolling = false
                }
            } else {
                errorMsg = res.exceptionOrNull()?.message
            }
            delay(2500)
        }
    }

    val currentStatus = job?.status ?: "queued"
    val isCompleted = currentStatus == "completed"
    val isFailed = currentStatus == "failed"

    Scaffold(
        topBar = {
            AutoClipTopBar(
                title = "Job #$jobId",
                canNavigateBack = true,
                onNavigateBack = onNavigateBack
            )
        },
        containerColor = BrandNavyBg
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            // Status Card
            Card(
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = BrandCardSurface),
                border = androidx.compose.foundation.BorderStroke(1.dp, BrandBorder),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(
                    modifier = Modifier.padding(18.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            text = "Pipeline Status",
                            style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                            color = BrandTextPrimary
                        )
                        StatusBadge(currentStatus)
                    }

                    Text(
                        text = job?.message?.ifBlank { "Processing on Windows PC..." } ?: "Contacting PC server...",
                        style = MaterialTheme.typography.bodyMedium,
                        color = BrandMintAccent
                    )

                    if (!isCompleted && !isFailed) {
                        LinearProgressIndicator(
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(6.dp)
                                .clip(RoundedCornerShape(3.dp)),
                            color = BrandMintAccent,
                            trackColor = BrandCardElevated
                        )
                    }
                }
            }

            // Visual Pipeline Stages
            Card(
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = BrandCardSurface),
                border = androidx.compose.foundation.BorderStroke(1.dp, BrandBorder),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(
                    modifier = Modifier.padding(18.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp)
                ) {
                    Text(
                        text = "Automated Stages",
                        style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold),
                        color = BrandTextPrimary
                    )

                    PipelineStageRow(
                        title = "1. Source Acquisition",
                        subtitle = "yt-dlp video stream fetch on PC",
                        isDone = currentStatus in listOf("transcribing", "completed"),
                        isActive = currentStatus == "downloading"
                    )

                    PipelineStageRow(
                        title = "2. Faster-Whisper Speech Recognition",
                        subtitle = "Word-level timestamped transcription",
                        isDone = currentStatus == "completed",
                        isActive = currentStatus == "transcribing"
                    )

                    PipelineStageRow(
                        title = "3. Ollama Highlight & Viral Hook Engine",
                        subtitle = "Context evaluation & scene retention scoring",
                        isDone = currentStatus == "completed",
                        isActive = currentStatus == "transcribing"
                    )

                    PipelineStageRow(
                        title = "4. FFmpeg 9:16 Video Rendering",
                        subtitle = "Vertical crop with burned-in dynamic captions",
                        isDone = currentStatus == "completed",
                        isActive = currentStatus == "uploading"
                    )

                    PipelineStageRow(
                        title = "5. Post-Production Intelligence Studio",
                        subtitle = "5 title options, AI hook, and scene intelligence",
                        isDone = isCompleted,
                        isActive = isCompleted
                    )
                }
            }

            // Completed Action
            if (isCompleted && (job?.clips?.isNotEmpty() == true)) {
                Button(
                    onClick = { onOpenStudio(jobId, 0) },
                    shape = RoundedCornerShape(14.dp),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = BrandMintAccent,
                        contentColor = Color(0xFF041913)
                    ),
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(52.dp)
                ) {
                    Icon(imageVector = Icons.Default.PlayArrow, contentDescription = null)
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(
                        text = "Open Viral Shorts Studio (${job?.clips?.size} Clips)",
                        style = MaterialTheme.typography.labelLarge.copy(fontWeight = FontWeight.Bold)
                    )
                }
            }

            // Error Message
            if (isFailed || errorMsg != null) {
                Card(
                    shape = RoundedCornerShape(16.dp),
                    colors = CardDefaults.cardColors(containerColor = BrandError.copy(alpha = 0.15f)),
                    border = androidx.compose.foundation.BorderStroke(1.dp, BrandError),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Icon(Icons.Default.Error, contentDescription = null, tint = BrandError)
                            Text(
                                text = "Pipeline Error",
                                style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold),
                                color = BrandError
                            )
                        }
                        Spacer(modifier = Modifier.height(6.dp))
                        Text(
                            text = job?.message ?: errorMsg ?: "Unknown error occurred on backend.",
                            style = MaterialTheme.typography.bodySmall,
                            color = BrandTextPrimary
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun PipelineStageRow(
    title: String,
    subtitle: String,
    isDone: Boolean,
    isActive: Boolean
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Box(
            modifier = Modifier
                .size(24.dp)
                .clip(CircleShape)
                .background(
                    when {
                        isDone -> BrandSuccess
                        isActive -> BrandMintAccent
                        else -> BrandCardElevated
                    }
                ),
            contentAlignment = Alignment.Center
        ) {
            if (isDone) {
                Icon(
                    imageVector = Icons.Default.CheckCircle,
                    contentDescription = null,
                    tint = Color(0xFF041913),
                    modifier = Modifier.size(16.dp)
                )
            } else if (isActive) {
                CircularProgressIndicator(
                    modifier = Modifier.size(14.dp),
                    color = Color(0xFF041913),
                    strokeWidth = 2.dp
                )
            }
        }

        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = title,
                style = MaterialTheme.typography.bodyMedium.copy(
                    fontWeight = if (isActive || isDone) FontWeight.Bold else FontWeight.Normal
                ),
                color = if (isActive) BrandMintAccent else BrandTextPrimary
            )
            Text(
                text = subtitle,
                style = MaterialTheme.typography.labelSmall,
                color = BrandTextSecondary
            )
        }
    }
}
