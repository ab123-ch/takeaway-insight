from datetime import UTC, date, datetime

from takeaway_insight.db import Database
from takeaway_insight.models import Merchant, SyncRun
from takeaway_insight.services import InsightService
from takeaway_insight.worker import due_sync_dates


def test_worker_rechecks_yesterday_after_midnight_but_does_not_skip_today():
    db = Database("sqlite://")
    db.create_schema()
    service = InsightService(db)
    service.create_merchant(name="门店", platform="eleme", external_shop_id="1", enabled=True)
    with db.session() as session:
        session.add(SyncRun(merchant_id=1, target_date=date(2026, 11, 16), status="success"))
        session.commit()
    now = datetime(2026, 11, 16, 16, 1, tzinfo=UTC)
    assert due_sync_dates(db, now) == [(1, [date(2026, 11, 16), date(2026, 11, 17)])]
    with db.session() as session:
        session.add(SyncRun(merchant_id=1, target_date=date(2026, 11, 17), status="success"))
        session.commit()
    assert due_sync_dates(db, now) == [(1, [date(2026, 11, 17)])]
    with db.session() as session:
        session.get(Merchant, 1).enabled = False
        session.commit()
    assert due_sync_dates(db, now) == []
    db.dispose()
