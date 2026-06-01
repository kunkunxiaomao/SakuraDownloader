from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import AppSettings
from .models import CrawlTask


class ScheduleService:
    def __init__(self, settings: AppSettings, submit_callback) -> None:
        self.settings = settings
        self.submit_callback = submit_callback
        self.scheduler = AsyncIOScheduler()

    def start(self) -> None:
        for item in self.settings.schedules:
            if item.trigger == "cron" and item.cron:
                trigger = CronTrigger.from_crontab(item.cron)
            else:
                seconds = item.seconds or 0
                minutes = item.minutes or 0
                trigger = IntervalTrigger(seconds=seconds, minutes=minutes)
            self.scheduler.add_job(self._submit_job, trigger=trigger, args=[item], id=item.name, replace_existing=True)
        self.scheduler.start()

    async def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)

    async def _submit_job(self, item) -> None:
        payload = dict(item.task)
        task = CrawlTask.create(
            url=payload["url"],
            spider=payload.get("spider", self.settings.master.default_spider),
            method=payload.get("method", "GET"),
            headers=payload.get("headers"),
            metadata=payload.get("metadata"),
            body=payload.get("body"),
            priority=item.priority,
            schedule_name=item.name,
        )
        await self.submit_callback(task)
