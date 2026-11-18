"""独立调度进程。Web 多 worker 不会重复创建调度器。"""

from datetime import timedelta
from zoneinfo import ZoneInfo

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
    from takeaway_insight.agent_jobs import AgentJobs, run_agent_worker

    await run_agent_worker(AgentJobs(service.db, settings))
