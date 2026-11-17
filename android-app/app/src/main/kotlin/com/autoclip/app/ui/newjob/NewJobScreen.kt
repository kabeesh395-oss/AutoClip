package com.autoclip.app.ui.newjob

import android.widget.Toast
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.VideoLibrary
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.autoclip.app.data.ApiClient
import com.autoclip.app.data.PreferencesManager
import com.autoclip.app.data.models.JobDto
import com.autoclip.app.ui.components.AutoClipTopBar
import com.autoclip.app.ui.theme.*
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NewJobScreen(
    onNavigateBack: () -> Unit,
    onJobCreated: (String) -> Unit
) {
    val context = LocalContext.current
    val prefs = remember { PreferencesManager(context) }
    val apiClient = remember { ApiClient() }
    val scope = rememberCoroutineScope()

    var url by remember { mutableStateOf("") }
    var selectedLanguage by remember { mutableStateOf("") }
    var selectedModel by remember { mutableStateOf("small") }
    var clipCount by remember { mutableIntStateOf(5) }
    var isSubmitting by remember { mutableStateOf(false) }

    val languages = listOf(
        "" to "Auto Detect",
        "en" to "English",
        "ta" to "Tamil",
        "hi" to "Hindi",
        "es" to "Spanish",
        "fr" to "French",
        "de" to "German"
    )

    val models = listOf("tiny", "base", "small", "medium")

    fun submit() {
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            Toast.makeText(context, "Please enter a valid http/https video URL", Toast.LENGTH_SHORT).show()
            return
        }

        isSubmitting = true
        val config = prefs.getServerConfig()
        scope.launch {
            val result = apiClient.submitJob(
                baseUrl = config.baseUrl,
                url = url.trim(),
                language = selectedLanguage,
                model = selectedModel,
                count = clipCount
            )
            isSubmitting = false
            if (result.isSuccess) {
                val job = result.getOrNull()!!
                prefs.saveJob(job)
                Toast.makeText(context, "Processing started on PC", Toast.LENGTH_SHORT).show()
                onJobCreated(job.id)
            } else {
                val err = result.exceptionOrNull()?.message ?: "Failed to connect to PC backend"
                Toast.makeText(context, "Error: $err", Toast.LENGTH_LONG).show()
            }
        }
    }

    Scaffold(
        topBar = {
            AutoClipTopBar(
                title = "New Viral Short",
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
            Card(
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = BrandCardSurface),
                border = androidx.compose.foundation.BorderStroke(1.dp, BrandBorder),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(
                    modifier = Modifier.padding(18.dp),
                    verticalArrangement = Arrangement.spacedBy(16.dp)
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        Icon(
                            imageVector = Icons.Default.VideoLibrary,
                            contentDescription = null,
                            tint = BrandMintAccent,
                            modifier = Modifier.size(22.dp)
                        )
                        Text(
                            text = "Video / Stream Source",
                            style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                            color = BrandTextPrimary
                        )
                    }

                    OutlinedTextField(
                        value = url,
                        onValueChange = { url = it },
                        label = { Text("Video URL (YouTube / Stream / VOD)") },
                        placeholder = { Text("https://www.youtube.com/watch?v=...") },
                        singleLine = true,
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = BrandMintAccent,
                            unfocusedBorderColor = BrandBorder,
                            focusedTextColor = BrandTextPrimary,
                            unfocusedTextColor = BrandTextPrimary
                        ),
                        modifier = Modifier.fillMaxWidth()
                    )

                    Text(
                        text = "AutoClip will download the video on your PC, generate high-quality audio transcription, select context-complete highlights, and apply 9:16 vertical styling with subtitles.",
                        style = MaterialTheme.typography.bodySmall,
                        color = BrandTextSecondary
                    )
                }
            }

            // AI Options Card
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
                        text = "AI Processing Parameters",
                        style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold),
                        color = BrandTextPrimary
                    )

                    // Language Chips
                    Text(
                        text = "Spoken Audio Language",
                        style = MaterialTheme.typography.labelMedium,
                        color = BrandTextSecondary
                    )
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        languages.take(4).forEach { (code, name) ->
                            FilterChip(
                                selected = selectedLanguage == code,
                                onClick = { selectedLanguage = code },
                                label = { Text(name, fontSize = 12.sp) },
                                colors = FilterChipDefaults.filterChipColors(
                                    selectedContainerColor = BrandMintGlow,
                                    selectedLabelColor = BrandMintAccent,
                                    containerColor = BrandCardElevated,
                                    labelColor = BrandTextPrimary
                                )
                            )
                        }
                    }

                    // Whisper Model Chips
                    Text(
                        text = "Faster-Whisper Model",
                        style = MaterialTheme.typography.labelMedium,
                        color = BrandTextSecondary
                    )
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        models.forEach { m ->
                            FilterChip(
                                selected = selectedModel == m,
                                onClick = { selectedModel = m },
                                label = { Text(m.replaceFirstChar { it.uppercase() }, fontSize = 12.sp) },
                                colors = FilterChipDefaults.filterChipColors(
                                    selectedContainerColor = BrandSkyDark,
                                    selectedLabelColor = BrandSkyAccent,
                                    containerColor = BrandCardElevated,
                                    labelColor = BrandTextPrimary
                                )
                            )
                        }
                    }

                    // Clip Count Picker
                    Text(
                        text = "Max Shorts Count: $clipCount",
                        style = MaterialTheme.typography.labelMedium,
                        color = BrandTextSecondary
                    )
                    Slider(
                        value = clipCount.toFloat(),
                        onValueChange = { clipCount = it.toInt() },
                        valueRange = 1f..10f,
                        steps = 8,
                        colors = SliderDefaults.colors(
                            thumbColor = BrandMintAccent,
                            activeTrackColor = BrandMintAccent,
                            inactiveTrackColor = BrandCardElevated
                        )
                    )
                }
            }

            // Submit Button
            Button(
                onClick = { submit() },
                enabled = !isSubmitting && url.isNotBlank(),
                shape = RoundedCornerShape(14.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = BrandMintAccent,
                    contentColor = Color(0xFF041913)
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .height(52.dp)
            ) {
                if (isSubmitting) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(20.dp),
                        color = Color(0xFF041913),
                        strokeWidth = 2.dp
                    )
                    Spacer(modifier = Modifier.width(10.dp))
                    Text("Submitting to PC...", fontWeight = FontWeight.Bold)
                } else {
                    Icon(imageVector = Icons.Default.PlayArrow, contentDescription = null)
                    Spacer(modifier = Modifier.width(8.dp))
                    Text("Start AI Processing on PC", fontWeight = FontWeight.Bold)
                }
            }
        }
    }
}
