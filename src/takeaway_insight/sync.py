"""完整分页后原子入库；数据库租约协调网页与独立定时进程。"""

import asyncio
import logging
from contextlib import suppress
from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy import or_, update

from takeaway_insight.availability import OFFICIAL_API_UNAVAILABLE
from takeaway_insight.config import Settings
from takeaway_insight.contracts import ConfigurationError, ProviderError
from takeaway_insight.models import Merchant, SyncRun, utcnow
from takeaway_insight.services import InsightService

logger = logging.getLogger(__name__)


def default_provider(platform):
    # 暂不可用：保留 providers 内的官方合同与单元测试，实际任务不得误报可接入。
    raise ConfigurationError(OFFICIAL_API_UNAVAILABLE)


class SyncRunner:
    def __init__(self, service: InsightService, settings: Settings, provider_factory=None):
        self.service = service
        self.settings = settings
        self.provider_factory = provider_factory or default_provider

    def _acquire(self, merchant_id, target_date, token):
        now = utcnow()
        with self.service.db.session() as session:
            merchant = self.service._merchant(session, merchant_id)
            if merchant.is_demo:
                raise ValueError("演示门店不可同步，请新增真实平台门店")
            result = session.execute(
                update(Merchant)
                .where(
                    Merchant.id == merchant_id,
                    or_(Merchant.sync_lock_until.is_(None), Merchant.sync_lock_until < now),
                )
                .values(
                    sync_lock_token=token,
                    sync_lock_until=now + timedelta(seconds=self.settings.sync_lease_seconds),
                )
            )
            if result.rowcount != 1:
                session.rollback()
                return None
            session.execute(
                update(SyncRun)
                .where(SyncRun.merchant_id == merchant_id, SyncRun.status == "running")
                .values(status="error", finished_at=now, error="上次同步进程中断，租约已过期")
            )
            run = SyncRun(merchant_id=merchant_id, target_date=target_date, status="running")
            session.add(run)
            session.commit()
            return run.id

    def _renew(self, merchant_id, token):
        with self.service.db.session() as session:
            result = session.execute(
                update(Merchant)
                .where(Merchant.id == merchant_id, Merchant.sync_lock_token == token)
                .values(
                    sync_lock_until=utcnow() + timedelta(seconds=self.settings.sync_lease_seconds)
                )
            )
            session.commit()
            if result.rowcount != 1:
                raise ProviderError("同步租约已失效，本次数据未写入")

    async def _heartbeat(self, merchant_id, token):
        while True:
            await asyncio.sleep(self.settings.sync_lease_seconds / 3)
            await asyncio.to_thread(self._renew, merchant_id, token)

    def _finish(self, merchant_id, run_id, token, result):
        with self.service.db.session() as session:
            run = session.get(SyncRun, run_id)
            run.status = result["status"]
            run.finished_at = utcnow()
            run.error = result.get("error", "")
            for key in ("fetched", "inserted", "updated"):
                setattr(run, key, result.get(key, 0))
            merchant = self.service._merchant(session, merchant_id)
            if merchant.sync_lock_token == token:
                merchant.sync_lock_token = None
                merchant.sync_lock_until = None
                merchant.next_sync_at = utcnow() + timedelta(minutes=merchant.sync_interval_minutes)
            session.commit()

    async def sync_day(self, merchant_id: int, target_date: date) -> dict:
        token = uuid4().hex
        run_id = await asyncio.to_thread(self._acquire, merchant_id, target_date, token)
        if run_id is None:
            return {"status": "busy", "error": "该门店正在同步，请稍后查看同步记录"}
        result = {
            "id": run_id,
            "merchant_id": merchant_id,
            "target_date": target_date.isoformat(),
            "status": "error",
            "fetched": 0,
            "inserted": 0,
            "updated": 0,
        }
        heartbeat = asyncio.create_task(self._heartbeat(merchant_id, token))
        cancelled = False
        try:
            connection = self.service.get_connection(merchant_id)
            orders = await self.provider_factory(connection.platform).fetch_orders(
                connection, target_date
            )
            if heartbeat.done():
                heartbeat.result()
            await asyncio.to_thread(self._renew, merchant_id, token)
            # 已启动的数据库事务必须结束后才释放租约；取消协程不会终止 to_thread。
            commit_task = asyncio.create_task(
                asyncio.to_thread(self.service.ingest_orders, merchant_id, orders, token)
            )
            while True:
                try:
                    counts = await asyncio.shield(commit_task)
                    break
                except asyncio.CancelledError:
                    cancelled = True
            result.update(counts, fetched=len(orders), status="success")
        except ProviderError as error:
            result["error"] = str(error)
        except asyncio.CancelledError:
            cancelled = True
            result["error"] = "同步任务被中断，请重新同步该日期"
        except Exception:
            # 不记录包含平台响应或凭据的异常字符串。
            logger.error("订单同步发生内部错误，merchant_id=%s run_id=%s", merchant_id, run_id)
            result["error"] = "订单处理发生内部错误，请根据同步记录编号检查服务日志"
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, ProviderError):
                await heartbeat
            await asyncio.to_thread(self._finish, merchant_id, run_id, token, result)
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def sync_range(self, merchant_id: int, start_date: date, end_date: date) -> list[dict]:
        if end_date < start_date or (end_date - start_date).days > 30:
            raise ValueError("补拉范围须为正向日期区间，最多31天")
        self.service.get_connection(merchant_id)
        results = []
        current = start_date
        while current <= end_date:
            result = await self.sync_day(merchant_id, current)
            results.append(result)
            if result["status"] != "success":
                break
            current += timedelta(days=1)
        return results
