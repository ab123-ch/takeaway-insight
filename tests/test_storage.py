from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from takeaway_insight.db import Database
from takeaway_insight.models import Merchant, Order


@pytest.fixture
def database(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'test.db'}")
    db.create_schema()
    yield db
    db.dispose()


def test_duplicate_external_order_is_unique_per_merchant(database):
    with database.session() as s:
        a = Merchant(name="一店", platform="eleme", external_shop_id="1")
        b = Merchant(name="二店", platform="jd", external_shop_id="1")
        s.add_all([a, b])
        s.flush()
        for merchant in (a, b):
            s.add(
                Order(
                    merchant_id=merchant.id,
                    external_id="same",
                    placed_at=datetime.now(UTC),
                    business_date=datetime.now(UTC).date(),
                )
            )
        s.commit()
        s.add(
            Order(
                merchant_id=a.id,
                external_id="same",
                placed_at=datetime.now(UTC),
                business_date=datetime.now(UTC).date(),
            )
        )
        with pytest.raises(IntegrityError):
            s.commit()


def test_sqlite_roundtrip_keeps_aware_timestamps(database):
    now = datetime(2026, 11, 17, 1, 2, 3, tzinfo=UTC)
    with database.session() as s:
        merchant = Merchant(name="一店", platform="eleme", external_shop_id="1", created_at=now)
        s.add(merchant)
        s.commit()
        s.refresh(merchant)
        assert merchant.created_at == now
        assert merchant.created_at.tzinfo is not None
