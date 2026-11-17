package com.autoclip.app

import androidx.compose.runtime.Composable
import androidx.navigation3.runtime.entryProvider
import androidx.navigation3.runtime.rememberNavBackStack
import androidx.navigation3.ui.NavDisplay
import com.autoclip.app.ui.dashboard.DashboardScreen
import com.autoclip.app.ui.newjob.NewJobScreen
import com.autoclip.app.ui.progress.JobProgressScreen
import com.autoclip.app.ui.server.ServerConfigScreen
import com.autoclip.app.ui.studio.ClipStudioScreen

@Composable
fun MainNavigation() {
    val backStack = rememberNavBackStack(DashboardNavKey)

    NavDisplay(
        backStack = backStack,
        onBack = { backStack.removeLastOrNull() },
        entryProvider = entryProvider {
            entry<DashboardNavKey> {
                DashboardScreen(
                    onNavigateToNewJob = { backStack.add(NewJobNavKey) },
                    onNavigateToServerConfig = { backStack.add(ServerConfigNavKey) },
                    onNavigateToJob = { jobId -> backStack.add(JobProgressNavKey(jobId)) },
                    onNavigateToStudio = { jobId, clipIdx -> backStack.add(ClipStudioNavKey(jobId, clipIdx)) }
                )
            }
            entry<ServerConfigNavKey> {
                ServerConfigScreen(
                    onNavigateBack = { backStack.removeLastOrNull() }
                )
            }
            entry<NewJobNavKey> {
                NewJobScreen(
                    onNavigateBack = { backStack.removeLastOrNull() },
                    onJobCreated = { jobId ->
                        backStack.removeLastOrNull()
                        backStack.add(JobProgressNavKey(jobId))
                    }
                )
            }
            entry<JobProgressNavKey> { key ->
                JobProgressScreen(
                    jobId = key.jobId,
                    onNavigateBack = { backStack.removeLastOrNull() },
                    onOpenStudio = { jobId, clipIdx ->
                        backStack.removeLastOrNull()
                        backStack.add(ClipStudioNavKey(jobId, clipIdx))
                    }
                )
            }
            entry<ClipStudioNavKey> { key ->
                ClipStudioScreen(
                    jobId = key.jobId,
                    initialClipIndex = key.clipIndex,
                    onNavigateBack = { backStack.removeLastOrNull() }
                )
            }
        }
    )
}
