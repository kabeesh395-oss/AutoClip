package com.autoclip.app.ui.server

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Wifi
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
import com.autoclip.app.data.models.HealthResponse
import com.autoclip.app.data.models.ServerConfig
import com.autoclip.app.ui.components.AutoClipTopBar
import com.autoclip.app.ui.theme.*
import kotlinx.coroutines.launch

@Composable
fun ServerConfigScreen(
    onNavigateBack: () -> Unit = {}
) {
    val context = LocalContext.current
    val prefs = remember { PreferencesManager(context) }
    val apiClient = remember { ApiClient() }
    val scope = rememberCoroutineScope()

    val currentConfig = remember { prefs.getServerConfig() }
    var host by remember { mutableStateOf(currentConfig.host) }
    var port by remember { mutableStateOf(currentConfig.port.toString()) }

    var isChecking by remember { mutableStateOf(false) }
    var testResult by remember { mutableStateOf<Result<HealthResponse>?>(null) }
    var latencyMs by remember { mutableLongStateOf(0L) }

    fun testConnection() {
        val testPort = port.toIntOrNull() ?: 8765
        val testBaseUrl = "http://${host.trim()}:$testPort"
        isChecking = true
        testResult = null
        scope.launch {
            val start = System.currentTimeMillis()
            val res = apiClient.checkHealth(testBaseUrl)
            latencyMs = System.currentTimeMillis() - start
            isChecking = false
            testResult = res
        }
    }

    Scaffold(
        topBar = {
            AutoClipTopBar(
                title = "Backend Connection",
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
            // Header card
            Card(
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = BrandCardSurface),
                border = androidx.compose.foundation.BorderStroke(1.dp, BrandBorder),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(18.dp)) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        Icon(
                            imageVector = Icons.Default.Wifi,
                            contentDescription = null,
                            tint = BrandMintAccent,
                            modifier = Modifier.size(24.dp)
                        )
                        Text(
                            text = "PC Server Connection",
                            style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                            color = BrandTextPrimary
                        )
                    }
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        text = "Connect to your Windows PC running the AutoClip backend over the same Wi-Fi network. Video processing, Whisper transcription, and Ollama AI run locally on your PC.",
                        style = MaterialTheme.typography.bodySmall,
                        color = BrandTextSecondary
                    )
                }
            }

            // Input Fields
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
                    OutlinedTextField(
                        value = host,
                        onValueChange = { host = it },
                        label = { Text("PC IP Address (e.g. 192.168.1.100)") },
                        supportingText = {
                            Text("Find on PC via 'ipconfig' (IPv4 address). For emulator, use 10.0.2.2")
                        },
                        singleLine = true,
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = BrandMintAccent,
                            unfocusedBorderColor = BrandBorder,
                            focusedTextColor = BrandTextPrimary,
                            unfocusedTextColor = BrandTextPrimary
                        ),
                        modifier = Modifier.fillMaxWidth()
                    )

                    OutlinedTextField(
                        value = port,
                        onValueChange = { port = it },
                        label = { Text("Port") },
                        supportingText = { Text("Default AutoClip port is 8765") },
                        singleLine = true,
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = BrandMintAccent,
                            unfocusedBorderColor = BrandBorder,
                            focusedTextColor = BrandTextPrimary,
                            unfocusedTextColor = BrandTextPrimary
                        ),
                        modifier = Modifier.fillMaxWidth()
                    )

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        Button(
                            onClick = { testConnection() },
                            enabled = !isChecking && host.isNotBlank(),
                            shape = RoundedCornerShape(12.dp),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = BrandCardElevated,
                                contentColor = BrandMintAccent
                            ),
                            modifier = Modifier.weight(1f)
                        ) {
                            if (isChecking) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(16.dp),
                                    color = BrandMintAccent,
                                    strokeWidth = 2.dp
                                )
                                Spacer(modifier = Modifier.width(8.dp))
                                Text("Pinging...")
                            } else {
                                Icon(
                                    imageVector = Icons.Default.Refresh,
                                    contentDescription = null,
                                    modifier = Modifier.size(18.dp)
                                )
                                Spacer(modifier = Modifier.width(6.dp))
                                Text("Test Ping")
                            }
                        }

                        Button(
                            onClick = {
                                val parsedPort = port.toIntOrNull() ?: 8765
                                prefs.saveServerConfig(ServerConfig(host.trim(), parsedPort))
                                onNavigateBack()
                            },
                            shape = RoundedCornerShape(12.dp),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = BrandMintAccent,
                                contentColor = Color(0xFF041913)
                            ),
                            modifier = Modifier.weight(1f)
                        ) {
                            Text(
                                "Save & Connect",
                                style = MaterialTheme.typography.labelLarge.copy(fontWeight = FontWeight.Bold)
                            )
                        }
                    }
                }
            }

            // Connection Test Result Card
            testResult?.let { result ->
                Card(
                    shape = RoundedCornerShape(16.dp),
                    colors = CardDefaults.cardColors(
                        containerColor = if (result.isSuccess) BrandMintGlow.copy(alpha = 0.3f) else BrandError.copy(alpha = 0.15f)
                    ),
                    border = androidx.compose.foundation.BorderStroke(
                        1.dp,
                        if (result.isSuccess) BrandMintAccent else BrandError
                    ),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(10.dp)
                        ) {
                            Icon(
                                imageVector = if (result.isSuccess) Icons.Default.CheckCircle else Icons.Default.Error,
                                contentDescription = null,
                                tint = if (result.isSuccess) BrandMintAccent else BrandError,
                                modifier = Modifier.size(24.dp)
                            )
                            Text(
                                text = if (result.isSuccess) "Connected Successfully ($latencyMs ms)" else "Connection Failed",
                                style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold),
                                color = if (result.isSuccess) BrandMintAccent else BrandError
                            )
                        }
                        Spacer(modifier = Modifier.height(8.dp))
                        if (result.isSuccess) {
                            val health = result.getOrNull()!!
                            Text(
                                text = "Server: ${health.app}\nStatus: ${health.status}\nMode: ${health.mode}\nEndpoint: http://${host.trim()}:${port.trim()}",
                                style = MaterialTheme.typography.bodySmall,
                                color = BrandTextPrimary
                            )
                        } else {
                            val err = result.exceptionOrNull()?.message ?: "Unknown error"
                            Text(
                                text = "Error: $err\n\nTroubleshooting tips:\n1. Ensure PC is running: python -m uvicorn autoclip_app.app:app --host 0.0.0.0 --port $port\n2. Phone and PC must be on the same Wi-Fi.\n3. Check Windows Firewall allows Python/port $port.",
                                style = MaterialTheme.typography.bodySmall,
                                color = BrandTextPrimary
                            )
                        }
                    }
                }
            }
        }
    }
}
