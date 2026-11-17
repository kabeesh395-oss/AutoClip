from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import PlatformType, PrivacyStatus, ProductionRules, PublishSchedule, PublishStatus
from .storage import DEFAULT_DB, ProductionStorage

logger = logging.getLogger("autoclip.production.calendar")


class CalendarManager:
    """
    Manages the content release calendar, timetable slots, and schedule lifecycle.
    """

    def __init__(self, storage: ProductionStorage | None = None) -> None:
        self.storage = storage or ProductionStorage()

    def suggest_next_slot(self, platform: str = "youtube_shorts") -> str:
        """
        Suggests the next optimal posting slot according to ProductionRules
        and existing calendar occupancy.
        """
        rules = self.storage.get_rules()
        slots = rules.preferred_slots or ["12:00", "16:00", "20:00"]
        now = datetime.now(timezone.utc)

        # Look up to 7 days ahead
        for day_offset in range(7):
            target_date = now.date() + timedelta(days=day_offset)
            date_str = target_date.strftime("%Y-%m-%d")

            # Check daily limit for platform
            count_day = self.storage.count_published_today(platform, date_prefix_utc=date_str)
            limit = {
                PlatformType.YOUTUBE_SHORTS.value: rules.daily_limit_shorts,
                PlatformType.YOUTUBE_LONGFORM.value: rules.daily_limit_longform,
                PlatformType.INSTAGRAM_REELS.value: rules.daily_limit_reels,
            }.get(platform, 3)

            if count_day >= limit:
                continue

            # Query existing schedules for this date
            day_schedules = self.storage.list_schedules(
                start_date=f"{date_str}T00:00:00Z",
                end_date=f"{date_str}T23:59:59Z",
                platform=platform,
            )
            booked_times = {s.scheduled_time_iso[:16] for s in day_schedules if s.status != PublishStatus.CANCELLED.value}

            for slot in slots:
                hh, mm = slot.split(":")
                slot_dt = datetime(target_date.year, target_date.month, target_date.day, int(hh), int(mm), tzinfo=timezone.utc)

                if slot_dt <= now:
                    continue  # in the past

                slot_prefix = slot_dt.strftime("%Y-%m-%dT%H:%M")
                if slot_prefix not in booked_times:
                    return slot_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Fallback: tomorrow at noon UTC
        fallback = (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
        return fallback.strftime("%Y-%m-%dT%H:%M:%SZ")

    def schedule_item(
        self,
        item_id: str,
        platform: str = "youtube_shorts",
        scheduled_time_iso: str | None = None,
        privacy_status: str = "private",
        rights_confirmed: bool = False,
    ) -> PublishSchedule:
        item = self.storage.get_content_item(item_id)
        if not item:
            raise ValueError(f"Content item {item_id} not found.")

        target_time = scheduled_time_iso or self.suggest_next_slot(platform)

        sched_id = f"sched_{uuid.uuid4().hex[:10]}"
        sched = PublishSchedule(
            id=sched_id,
            item_id=item_id,
            platform=platform,
            scheduled_time_iso=target_time,
            privacy_status=privacy_status,
            status=PublishStatus.SCHEDULED.value,
            rights_confirmed=rights_confirmed,
        )
        self.storage.create_schedule(sched)
        return sched

    def get_calendar_view(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        platform: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """
        Returns structured calendar entries grouped by day with content details and summary KPIs.
        """
        schedules = self.storage.list_schedules(
            start_date=start_date,
            end_date=end_date,
            platform=platform,
            status=status,
            limit=300,
        )

        days_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        status_counts = {"scheduled": 0, "published": 0, "failed": 0, "cancelled": 0, "draft": 0}

        for s in schedules:
            status_counts[s.status] = status_counts.get(s.status, 0) + 1
            item = self.storage.get_content_item(s.item_id)
            day_key = s.scheduled_time_iso[:10] if s.scheduled_time_iso else "undated"

            days_map[day_key].append({
                "schedule": s.to_dict(),
                "content_item": item.to_dict() if item else None,
            })

        return {
            "summary": {
                "total_items": len(schedules),
                "counts_by_status": status_counts,
                "days_count": len(days_map),
            },
            "days": dict(days_map),
            "schedules": [s.to_dict() for s in schedules],
        }

    def reschedule(self, schedule_id: str, new_time_iso: str) -> bool:
        return self.storage.reschedule(schedule_id, new_time_iso)

    def cancel(self, schedule_id: str) -> bool:
        return self.storage.cancel_schedule(schedule_id)

    def retry(self, schedule_id: str) -> bool:
        return self.storage.retry_schedule(schedule_id)
