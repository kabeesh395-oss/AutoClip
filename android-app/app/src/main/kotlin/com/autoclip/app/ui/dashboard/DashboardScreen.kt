package com.autoclip.app.ui.dashboard

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Movie
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.autoclip.app.data.ApiClient
import com.autoclip.app.data.PreferencesManager
import com.autoclip.app.data.models.JobDto
import com.autoclip.app.ui.components.AutoClipTopBar
import com.autoclip.app.ui.components.ServerStatusChip
import com.autoclip.app.ui.components.StatusBadge
import com.autoclip.app.ui.theme.*
import kotlinx.coroutines.launch

@Composable
fun DashboardScreen(
    onNavigateToNewJob: () -> Unit,
    onNavigateToServerConfig: () -> Unit,
    onNavigateToJob: (String) -> Unit,
    onNavigateToStudio: (String, Int) -> Unit
) {
    val context = LocalContext.current
    val prefs = remember { PreferencesManager(context) }
    val apiClient = remember { ApiClient() }
    val scope = rememberCoroutineScope()

    var isOnline by remember { mutableStateOf(false) }
    var serverHost by remember { mutableStateOf("") }
    var recentJobs by remember { mutableStateOf<List<JobDto>>(emptyList()) }
    var isRefreshing by remember { mutableStateOf(false) }

    fun refresh() {
        val config = prefs.getServerConfig()
        serverHost = config.baseUrl
        recentJobs = prefs.getRecentJobs()
        isRefreshing = true
        scope.launch {
            val healthRes = apiClient.checkHealth(config.baseUrl)
            isOnline = healthRes.isSuccess
            if (isOnline) {
                val historyRes = apiClient.getHistory(config.baseUrl)
                if (historyRes.isSuccess && historyRes.getOrNull()?.isNotEmpty() == true) {
                    val remoteJobs = historyRes.getOrNull()!!
                    recentJobs = remoteJobs
                    remoteJobs.forEach { prefs.saveJob(it) }
                }
            }
            isRefreshing = false
        }
    }

    LaunchedEffect(Unit) {
        refresh()
    }

    Scaffold(
        topBar = {
            AutoClipTopBar(
                title = "AutoClip AI",
                actions = {
                    ServerStatusChip(
                        isOnline = isOnline,
                        onClick = onNavigateToServerConfig,
                        modifier = Modifier.padding(end = 8.dp)
                    )
                    IconButton(onClick = onNavigateToServerConfig) {
                        Icon(
                            imageVector = Icons.Default.Settings,
                            contentDescription = "Settings",
                            tint = BrandTextPrimary
                        )
                    }
                }
            )
        },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = onNavigateToNewJob,
                containerColor = BrandMintAccent,
                contentColor = Color(0xFF041913),
                icon = { Icon(Icons.Default.Add, contentDescription = null) },
                text = {
                    Text(
                        "New Short",
                        style = MaterialTheme.typography.labelLarge.copy(fontWeight = FontWeight.Bold)
                    )
                }
            )
        },
        containerColor = BrandNavyBg
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            item {
                Spacer(modifier = Modifier.height(4.dp))
                // Hero Banner
                Card(
                    shape = RoundedCornerShape(20.dp),
                    colors = CardDefaults.cardColors(containerColor = Color.Transparent),
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(20.dp))
                        .background(
                            Brush.verticalGradient(
                                colors = listOf(Color(0xFF162544), BrandCardSurface)
                            )
                        )
                        .border(1.dp, BrandBorder, RoundedCornerShape(20.dp))
                ) {
                    Column(
                        modifier = Modifier.padding(20.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Icon(
                                imageVector = Icons.Default.AutoAwesome,
                                contentDescription = null,
                                tint = BrandMintAccent,
                                modifier = Modifier.size(20.dp)
                            )
                            Text(
                                text = "AI VIRAL SHORTS STUDIO",
                                style = MaterialTheme.typography.labelSmall.copy(
                                    fontWeight = FontWeight.ExtraBold,
                                    letterSpacing = 1.2.sp
                                ),
                                color = BrandMintAccent
                            )
                        }

                        Text(
                            text = "Turn long videos into high-retention vertical Shorts.",
                            style = MaterialTheme.typography.titleLarge.copy(
                                fontWeight = FontWeight.Bold,
                                letterSpacing = (-0.5).sp
                            ),
                            color = BrandTextPrimary
                        )

                        Text(
                            text = "Extract attention hooks, 5 title variants, word-level synchronized captions, and scene retention analytics directly from your phone.",
                            style = MaterialTheme.typography.bodySmall,
                            color = BrandTextSecondary
                        )

                        Spacer(modifier = Modifier.height(4.dp))

                        Button(
                            onClick = onNavigateToNewJob,
                            shape = RoundedCornerShape(12.dp),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = BrandMintAccent,
                                contentColor = Color(0xFF041913)
                            ),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Icon(Icons.Default.Add, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(modifier = Modifier.width(8.dp))
                            Text(
                                "Submit Video or Livestream URL",
                                style = MaterialTheme.typography.labelLarge.copy(fontWeight = FontWeight.Bold)
                            )
                        }
                    }
                }
            }

            // Batch Production Mode Card (SRS v1.0)
            item {
                Card(
                    shape = RoundedCornerShape(16.dp),
                    colors = CardDefaults.cardColors(containerColor = BrandCardSurface),
                    border = androidx.compose.foundation.BorderStroke(1.dp, BrandMintAccent.copy(alpha = 0.4f)),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                Text(
                                    text = "📦 Batch Production Mode",
                                    style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                                    color = BrandTextPrimary
                                )
                            }
                            StatusBadge("100% Approval")
                        }

                        Text(
                            text = "3-Day Scheduled Target: 9 Videos (2 Shorts + 1 Long / day). Independent cloud publishing via YouTube Data API.",
                            style = MaterialTheme.typography.bodySmall,
                            color = BrandTextSecondary
                        )

                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Box(
                                modifier = Modifier
                                    .weight(1f)
                                    .clip(RoundedCornerShape(8.dp))
                                    .background(Color(0xFF091122))
                                    .padding(vertical = 8.dp),
                                contentAlignment = Alignment.Center
                            ) {
                                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                    Text("9 Target", style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold), color = BrandMintAccent)
                                    Text("3 Days", style = MaterialTheme.typography.labelSmall, color = BrandTextMuted)
                                }
                            }
                            Box(
                                modifier = Modifier
                                    .weight(1f)
                                    .clip(RoundedCornerShape(8.dp))
                                    .background(Color(0xFF091122))
                                    .padding(vertical = 8.dp),
                                contentAlignment = Alignment.Center
                            ) {
                                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                    Text("2 Shorts + 1 Long", style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold), color = BrandSkyAccent)
                                    Text("Daily Output", style = MaterialTheme.typography.labelSmall, color = BrandTextMuted)
                                }
                            }
                            Box(
                                modifier = Modifier
                                    .weight(1f)
                                    .clip(RoundedCornerShape(8.dp))
                                    .background(Color(0xFF091122))
                                    .padding(vertical = 8.dp),
                                contentAlignment = Alignment.Center
                            ) {
                                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                    Text("Asia/Kolkata", style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold), color = Color(0xFFC084FC))
                                    Text("12 / 4 / 7 PM", style = MaterialTheme.typography.labelSmall, color = BrandTextMuted)
                                }
                            }
                        }
                    }
                }
            }

            // Quick Architecture Pipeline Stats
            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Card(
                        shape = RoundedCornerShape(14.dp),
                        colors = CardDefaults.cardColors(containerColor = BrandCardSurface),
                        border = androidx.compose.foundation.BorderStroke(1.dp, BrandBorder),
                        modifier = Modifier.weight(1f)
                    ) {
                        Column(modifier = Modifier.padding(14.dp)) {
                            Text(
                                text = "Transcription",
                                style = MaterialTheme.typography.labelSmall,
                                color = BrandTextSecondary
                            )
                            Spacer(modifier = Modifier.height(4.dp))
                            Text(
                                text = "Faster-Whisper",
                                style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Bold),
                                color = BrandTextPrimary
                            )
                            Text(
                                text = "Word-level Timestamps",
                                style = MaterialTheme.typography.labelSmall,
                                color = BrandMintAccent
                            )
                        }
                    }

                    Card(
                        shape = RoundedCornerShape(14.dp),
                        colors = CardDefaults.cardColors(containerColor = BrandCardSurface),
                        border = androidx.compose.foundation.BorderStroke(1.dp, BrandBorder),
                        modifier = Modifier.weight(1f)
                    ) {
                        Column(modifier = Modifier.padding(14.dp)) {
                            Text(
                                text = "Scene AI",
                                style = MaterialTheme.typography.labelSmall,
                                color = BrandTextSecondary
                            )
                            Spacer(modifier = Modifier.height(4.dp))
                            Text(
                                text = "Ollama (qwen2.5)",
                                style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Bold),
                                color = BrandTextPrimary
                            )
                            Text(
                                text = "Viral Hook Detection",
                                style = MaterialTheme.typography.labelSmall,
                                color = BrandSkyAccent
                            )
                        }
                    }
                }
            }

            // History Header
            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Icon(
                            imageVector = Icons.Default.History,
                            contentDescription = null,
                            tint = BrandTextSecondary,
                            modifier = Modifier.size(18.dp)
                        )
                        Text(
                            text = "Recent Processing Jobs",
                            style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                            color = BrandTextPrimary
                        )
                    }

                    IconButton(onClick = { refresh() }) {
                        Icon(
                            imageVector = Icons.Default.Refresh,
                            contentDescription = "Refresh",
                            tint = BrandMintAccent
                        )
                    }
                }
            }

            if (recentJobs.isEmpty()) {
                item {
                    Card(
                        shape = RoundedCornerShape(16.dp),
                        colors = CardDefaults.cardColors(containerColor = BrandCardSurface),
                        border = androidx.compose.foundation.BorderStroke(1.dp, BrandBorder),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Column(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(28.dp),
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.spacedBy(10.dp)
                        ) {
                            Icon(
                                imageVector = Icons.Default.Movie,
                                contentDescription = null,
                                tint = BrandTextMuted,
                                modifier = Modifier.size(40.dp)
                            )
                            Text(
                                text = "No clips generated yet",
                                style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold),
                                color = BrandTextSecondary
                            )
                            Text(
                                text = "Submit a video link to create your first set of viral Shorts.",
                                style = MaterialTheme.typography.bodySmall,
                                color = BrandTextMuted
                            )
                        }
                    }
                }
            } else {
                items(recentJobs, key = { it.id }) { job ->
                    Card(
                        shape = RoundedCornerShape(14.dp),
                        colors = CardDefaults.cardColors(containerColor = BrandCardSurface),
                        border = androidx.compose.foundation.BorderStroke(1.dp, BrandBorder),
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable {
                                if (job.status == "completed" && job.clips.isNotEmpty()) {
                                    onNavigateToStudio(job.id, 0)
                                } else {
                                    onNavigateToJob(job.id)
                                }
                            }
                    ) {
                        Column(
                            modifier = Modifier.padding(16.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text(
                                    text = "Job #${job.id}",
                                    style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold),
                                    color = BrandSkyAccent
                                )
                                StatusBadge(job.status)
                            }

                            Text(
                                text = job.source.ifBlank { "Local Upload" },
                                style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold),
                                color = BrandTextPrimary,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis
                            )

                            Text(
                                text = job.message.ifBlank { "Job created" },
                                style = MaterialTheme.typography.bodySmall,
                                color = BrandTextSecondary,
                                maxLines = 2,
                                overflow = TextOverflow.Ellipsis
                            )

                            if (job.clips.isNotEmpty()) {
                                Row(
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                                ) {
                                    Icon(
                                        imageVector = Icons.Default.PlayArrow,
                                        contentDescription = null,
                                        tint = BrandMintAccent,
                                        modifier = Modifier.size(16.dp)
                                    )
                                    Text(
                                        text = "${job.clips.size} Shorts Ready · Open Studio",
                                        style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold),
                                        color = BrandMintAccent
                                    )
                                }
                            }
                        }
                    }
                }
            }

            item {
                Spacer(modifier = Modifier.height(72.dp))
            }
        }
    }
}
