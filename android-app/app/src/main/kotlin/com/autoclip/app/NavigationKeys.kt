package com.autoclip.app

import androidx.navigation3.runtime.NavKey
import kotlinx.serialization.Serializable

@Serializable
data object DashboardNavKey : NavKey

@Serializable
data object ServerConfigNavKey : NavKey

@Serializable
data object NewJobNavKey : NavKey

@Serializable
data class JobProgressNavKey(val jobId: String) : NavKey

@Serializable
data class ClipStudioNavKey(val jobId: String, val clipIndex: Int = 0) : NavKey

@Serializable
data object HistoryNavKey : NavKey
