import asyncio
import threading
from datetime import UTC, date, datetime

import pytest

from takeaway_insight.config import Settings
from takeaway_insight.contracts import NormalizedLine, NormalizedOrder, ProviderError
from takeaway_insight.db import Database
from takeaway_insight.services import InsightService
from takeaway_insight.sync import SyncRunner


@pytest.fixture
def service(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/sync.db")
    db.create_schema()
    service = InsightService(db)
    service.create_merchant(name="测试门店", platform="eleme", external_shop_id="123", enabled=True)
    yield service
    db.dispose()


class FakeProvider:
    calls = 0

    async def fetch_orders(self, connection, target_date):
        self.calls += 1
        return [
            NormalizedOrder(
                external_id="order-1",
                status="completed",
                placed_at=datetime(2026, 11, 17, 6, tzinfo=UTC),
                items=[NormalizedLine(external_id="line-1", name="半只鸡+半只鸭", quantity=2)],
            )
        ]


async def test_sync_logs_and_repeated_run_is_idempotent(service):
    provider = FakeProvider()
    runner = SyncRunner(service, Settings(), lambda _: provider)
    first = await runner.sync_day(1, date(2026, 11, 17))
    second = await runner.sync_day(1, date(2026, 11, 17))
    assert first["status"] == second["status"] == "success"
    assert first["inserted"] == 1
    assert second["updated"] == 1
    assert service.get_orders(date(2026, 11, 17), date(2026, 11, 17))["total"] == 1
    assert len(service.get_sync_status()) == 2


async def test_failed_fetch_is_visible_and_does_not_write_orders(service):
    class BrokenProvider:
        async def fetch_orders(self, connection, target_date):
            raise ProviderError("订单第2页获取失败")

    result = await SyncRunner(service, Settings(), lambda _: BrokenProvider()).sync_day(
        1, date(2026, 11, 17)
    )
    assert result["status"] == "error"
    assert "第2页" in result["error"]
    assert service.get_orders(date(2026, 11, 17), date(2026, 11, 17))["total"] == 0


async def test_concurrent_sync_of_same_merchant_only_fetches_once(service):
    started, release = asyncio.Event(), asyncio.Event()

    class SlowProvider(FakeProvider):
        async def fetch_orders(self, connection, target_date):
            started.set()
            await release.wait()
            return await super().fetch_orders(connection, target_date)

    provider = SlowProvider()
    runner = SyncRunner(service, Settings(), lambda _: provider)
    active = asyncio.create_task(runner.sync_day(1, date(2026, 11, 17)))
    await started.wait()
    duplicate = await runner.sync_day(1, date(2026, 11, 17))
    release.set()
    await active
    assert duplicate["status"] == "busy"
    assert provider.calls == 1


async def test_sync_range_rejects_invalid_range(service):
    runner = SyncRunner(service, Settings(), lambda _: FakeProvider())
    with pytest.raises(ValueError, match="31"):
        await runner.sync_range(1, date(2026, 1, 1), date(2026, 3, 1))
    with pytest.raises(ValueError, match="门店不存在"):
        await runner.sync_day(999, date(2026, 11, 17))


async def test_cancellation_waits_for_atomic_commit_and_keeps_lease(service):
    from takeaway_insight.models import Merchant

    started, release = threading.Event(), threading.Event()
    real_ingest = service.ingest_orders

    def blocked_ingest(*args):
        started.set()
        release.wait(5)
        return real_ingest(*args)

    service.ingest_orders = blocked_ingest
    runner = SyncRunner(service, Settings(), lambda _: FakeProvider())
    active = asyncio.create_task(runner.sync_day(1, date(2026, 11, 17)))
    await asyncio.to_thread(started.wait, 3)
    active.cancel()
    await asyncio.sleep(0.02)
    with service.db.session() as session:
        assert session.get(Merchant, 1).sync_lock_token
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await active
    assert service.get_sync_status()[0]["status"] == "success"
    assert service.get_orders(date(2026, 11, 17), date(2026, 11, 17))["total"] == 1


async def test_edit_merchant_and_acquire_lease_are_serialized(service):
    started, release = threading.Event(), threading.Event()
    original = service._merchant

    def paused_read(session, merchant_id):
        merchant = original(session, merchant_id)
        if threading.current_thread().name == "merchant-edit":
            started.set()
            release.wait(5)
        return merchant

    service._merchant = paused_read
    errors = []

    def edit():
        try:
            service.update_merchant(1, name="门店", platform="jd", external_shop_id="other")
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=edit, name="merchant-edit")
    thread.start()
    await asyncio.to_thread(started.wait, 3)
    runner = SyncRunner(service, Settings(), lambda _: FakeProvider())
    acquisition = asyncio.create_task(
        asyncio.to_thread(runner._acquire, 1, date(2026, 11, 17), "token")
    )
    try:
        await asyncio.sleep(0.04)
        assert not acquisition.done(), "门店编辑事务应阻止并发取得旧配置的租约"
    finally:
        release.set()
        await asyncio.to_thread(thread.join, 3)
        await acquisition
    assert not errors
    assert service.get_connection(1).platform == "jd"


async def test_expired_worker_cannot_commit_after_new_worker_takes_lease(service):
    from datetime import timedelta

    from takeaway_insight.models import Merchant, utcnow

    started, release = threading.Event(), threading.Event()
    original = service.ingest_orders

    def paused_ingest(*args):
        started.set()
        release.wait(5)
        return original(*args)

    service.ingest_orders = paused_ingest
    runner = SyncRunner(service, Settings(), lambda _: FakeProvider())
    active = asyncio.create_task(runner.sync_day(1, date(2026, 11, 17)))
    await asyncio.to_thread(started.wait, 3)
    with service.db.session() as session:
        session.get(Merchant, 1).sync_lock_until = utcnow() - timedelta(seconds=1)
        session.commit()
    await asyncio.to_thread(runner._acquire, 1, date(2026, 11, 17), "new-owner")
    release.set()
    result = await active
    assert result["status"] == "error"
    assert "租约" in result["error"]
    assert service.get_orders(date(2026, 11, 17), date(2026, 11, 17))["total"] == 0
    with service.db.session() as session:
        assert session.get(Merchant, 1).sync_lock_token == "new-owner"
