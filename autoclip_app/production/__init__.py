from __future__ import annotations

from .analytics import AnalyticsEngine
from .calendar import CalendarManager
from .content_producer import ContentProducer, SubtitleTimingValidator, compute_file_sha256
from .models import (
    ContentItem,
    PlatformAnalytics,
    PlatformType,
    PrivacyStatus,
    ProductionRules,
    PublishSchedule,
    PublishStatus,
)
from .multi_publisher import (
    DuplicateUploadError,
    InstagramReelsPublisher,
    MultiPublisherRouter,
    PublisherError,
    QuotaExceededError,
    RightsConfirmationRequiredError,
    YouTubePublisher,
)
from .service import ProductionService
from .storage import ProductionStorage, init_production_tables
from .storage_cleaner import (
    CleanupCandidate,
    CleanupHistoryItem,
    StorageCleanupManager,
    StorageCleanupSettings,
    StorageUsageReport,
)

# Global singleton instance for app-wide access
production_service = ProductionService()

__all__ = [
    "AnalyticsEngine",
    "CalendarManager",
    "CleanupCandidate",
    "CleanupHistoryItem",
    "ContentItem",
    "ContentProducer",
    "DuplicateUploadError",
    "InstagramReelsPublisher",
    "MultiPublisherRouter",
    "PlatformAnalytics",
    "PlatformType",
    "PrivacyStatus",
    "ProductionRules",
    "ProductionService",
    "ProductionStorage",
    "PublisherError",
    "PublishSchedule",
    "PublishStatus",
    "QuotaExceededError",
    "RightsConfirmationRequiredError",
    "StorageCleanupManager",
    "StorageCleanupSettings",
    "StorageUsageReport",
    "SubtitleTimingValidator",
    "YouTubePublisher",
    "compute_file_sha256",
    "init_production_tables",
    "production_service",
]
