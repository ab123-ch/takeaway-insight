"""独立调度进程。Web 多 worker 不会重复创建调度器。"""

import asyncio
from datetime import timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, or_, select

from takeaway_insight.models import Merchant, SyncRun, utcnow


def due_sync_dates(database, now=None):
    now = now or utcnow()
    result = []
    with database.session() as session:
        merchants = session.scalars(
            select(Merchant).where(
                Merchant.enabled.is_(True),
                or_(Merchant.next_sync_at.is_(None), Merchant.next_sync_at <= now),
            )
        )
        for merchant in merchants:
            today = now.astimezone(ZoneInfo(merchant.timezone)).date()
            latest = session.scalar(
                select(func.max(SyncRun.target_date)).where(
                    SyncRun.merchant_id == merchant.id, SyncRun.status == "success"
                )
            )
            dates = [today]
            if latest == today - timedelta(days=1):
                dates.insert(0, latest)
            result.append((merchant.id, dates))
    return result


async def run_worker(service, settings):
    from takeaway_insight.sync import SyncRunner

    runner = SyncRunner(service, settings)
    semaphore = asyncio.Semaphore(4)

    async def run_merchant(merchant_id, dates):
        async with semaphore:
            for target in dates:
                await runner.sync_day(merchant_id, target)

    async def tick():
        work = await asyncio.to_thread(due_sync_dates, service.db)
        await asyncio.gather(*(run_merchant(merchant_id, dates) for merchant_id, dates in work))

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        tick,
        "interval",
        seconds=30,
        max_instances=1,
        coalesce=True,
        next_run_time=utcnow(),
        misfire_grace_time=60,
    )
    scheduler.start()
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)
