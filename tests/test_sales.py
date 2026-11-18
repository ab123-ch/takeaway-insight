from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from takeaway_insight.contracts import NormalizedLine, NormalizedOrder
from takeaway_insight.db import Database
from takeaway_insight.services import InsightService


@pytest.fixture
def service(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'sales.db'}")
    db.create_schema()
    yield InsightService(db)
    db.dispose()


def test_current_platforms_only_support_china_business_day(service):
    with pytest.raises(ValueError, match="时区"):
        service.create_merchant(name="店", platform="eleme", external_shop_id="1", timezone="UTC")


def test_product_created_after_a_stale_lookup_is_reused(service, monkeypatch):
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from takeaway_insight.models import Product

    with service.db.session() as session:
        session.add_all([Product(name="半只鸡", unit="份"), Product(name="半只鸭", unit="份")])
        session.commit()
    original = Session.scalar
    missed = False

    def stale_lookup(session, statement, *args, **kwargs):
        nonlocal missed
        if (
            not missed
            and getattr(statement, "column_descriptions", [{}])[0].get("entity") is Product
        ):
            missed = True
            return None  # PostgreSQL并发事务在SELECT之后才插入相同商品的场景。
        return original(session, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "scalar", stale_lookup)
    merchant = service.create_merchant(name="店", platform="eleme", external_shop_id="1")
    service.ingest_orders(merchant["id"], [order()])
    with service.db.session() as session:
        assert session.scalar(select(func.count()).select_from(Product)) == 2


def order(status="completed", **kwargs):
    return NormalizedOrder(
        external_id="order-1",
        placed_at=datetime(2026, 11, 16, 23, 30, tzinfo=UTC),
        status=status,
        total_amount="80",
        items=[NormalizedLine(external_id="item-1", name="半只鸡+半只鸭", quantity=2)],
        **kwargs,
    )


def test_different_units_are_never_summed_as_one_quantity(service):
    merchant = service.create_merchant(name="一店", platform="eleme", external_shop_id="123")
    snapshot = order()
    snapshot.items.append(
        NormalizedLine(external_id="item-2", name="鸭肉", quantity="1.5", unit="千克")
    )
    service.ingest_orders(merchant["id"], [snapshot])
    report = service.get_sales_summary(date(2026, 11, 17), date(2026, 11, 17))
    assert report["summary"]["item_quantity"] is None
    assert report["summary"]["quantity_by_unit"] == [
        {"unit": "份", "quantity": "4", "review_quantity": "4"},
        {"unit": "千克", "quantity": "1.5", "review_quantity": "0"},
    ]
    assert {r["unit"] for r in report["trend"]} == {"份", "千克"}


def test_merchant_identity_and_timezone_cannot_change_after_orders_exist(service):
    merchant = service.create_merchant(name="一店", platform="eleme", external_shop_id="123")
    service.ingest_orders(merchant["id"], [order()])
    with pytest.raises(ValueError, match="历史订单|时区"):
        service.update_merchant(merchant["id"], name="一店", platform="jd", external_shop_id="123")
    with pytest.raises(ValueError, match="历史订单|时区"):
        service.update_merchant(
            merchant["id"], name="一店", platform="eleme", external_shop_id="456"
        )
    with pytest.raises(ValueError, match="历史订单|时区"):
        service.update_merchant(
            merchant["id"], name="一店", platform="eleme", external_shop_id="123", timezone="UTC"
        )


def test_undated_snapshot_cannot_restore_cancelled_order(service):
    merchant = service.create_merchant(name="店", platform="eleme", external_shop_id="1")
    service.ingest_orders(merchant["id"], [order(status="cancelled")])
    counts = service.ingest_orders(merchant["id"], [order()])
    assert counts["skipped"] == 1
    assert (
        service.get_sales_summary(date(2026, 11, 17), date(2026, 11, 17))["summary"][
            "item_quantity"
        ]
        == "0"
    )


def test_inconsistent_refund_and_adjusted_quantity_fails_atomically(service):
    merchant = service.create_merchant(name="店", platform="eleme", external_shop_id="1")
    snapshot = order(refund_status="partial", refund_amount="20")
    snapshot.items[0].refunded_quantity = Decimal("2")
    service.ingest_orders(merchant["id"], [snapshot])
    smaller = order()
    smaller.items[0].quantity = Decimal("1")
    with pytest.raises(ValueError, match="退款数量"):
        service.ingest_orders(merchant["id"], [smaller])
    assert (
        service.get_orders(date(2026, 11, 17), date(2026, 11, 17))["items"][0]["lines"][0][
            "quantity"
        ]
        == "2"
    )


def test_repeated_sync_updates_instead_of_duplicating_and_keeps_order_link(service):
    merchant = service.create_merchant(name="一店", platform="eleme", external_shop_id="123")
    assert service.ingest_orders(merchant["id"], [order()]) == {
        "inserted": 1,
        "updated": 0,
        "skipped": 0,
    }
    assert service.ingest_orders(merchant["id"], [order()]) == {
        "inserted": 0,
        "updated": 1,
        "skipped": 0,
    }
    report = service.get_sales_summary(date(2026, 11, 17), date(2026, 11, 17))
    assert report["summary"]["order_count"] == 1
    assert report["summary"]["revenue"] == "80.00"
    assert len(report["products"]) == 2
    assert {p["sold_quantity"] for p in report["products"]} == {"2"}
    data = service.get_orders(date(2026, 11, 17), date(2026, 11, 17))
    assert data["total"] == 1
    assert len(data["items"][0]["lines"][0]["components"]) == 2
    assert data["items"][0]["external_id"] == "order-1"


def test_merchant_filter_never_combines_other_shop_same_external_id(service):
    one = service.create_merchant(name="一店", platform="eleme", external_shop_id="1")
    two = service.create_merchant(name="二店", platform="jd", external_shop_id="1")
    for m in (one, two):
        service.ingest_orders(m["id"], [order()])
    report = service.get_sales_summary(
        date(2026, 11, 17), date(2026, 11, 17), merchant_id=one["id"]
    )
    assert report["summary"]["order_count"] == 1
    assert report["summary"]["item_quantity"] == "4"


def test_cancellation_removes_effective_sales_without_deleting_original_order(service):
    m = service.create_merchant(name="店", platform="eleme", external_shop_id="1")
    service.ingest_orders(m["id"], [order()])
    service.ingest_orders(m["id"], [order(status="cancelled")])
    report = service.get_sales_summary(date(2026, 11, 17), date(2026, 11, 17))
    assert report["summary"]["item_quantity"] == "0"
    assert report["summary"]["cancelled_count"] == 1
    assert service.get_orders(date(2026, 11, 17), date(2026, 11, 17))["total"] == 1


def test_rules_reprocess_history_and_confirmed_report(service):
    m = service.create_merchant(name="店", platform="eleme", external_shop_id="1")
    service.ingest_orders(m["id"], [order()])
    rule = service.create_rule(
        merchant_id=m["id"],
        match_name="半只鸡+半只鸭",
        components=[{"name": "半只鸡", "factor": "1"}, {"name": "半只鸭", "factor": "1"}],
    )
    assert rule["id"] > 0
    assert service.reprocess_orders(m["id"]) == 1
    report = service.get_sales_summary(date(2026, 11, 17), date(2026, 11, 17), confirmed_only=True)
    assert report["summary"]["item_quantity"] == "4"
    assert report["summary"]["review_quantity"] == "0"


def test_refund_updates_net_quantity_and_does_not_multiply_order_amount(service):
    m = service.create_merchant(name="店", platform="eleme", external_shop_id="1")
    data = order(refund_status="partial", refund_amount=20)
    data.items[0].refunded_quantity = Decimal("1")
    service.ingest_orders(m["id"], [data])
    report = service.get_sales_summary(date(2026, 11, 17), date(2026, 11, 17))
    assert report["summary"]["item_quantity"] == "2"
    assert report["summary"]["revenue"] == "60.00"


def test_jd_missing_after_sales_coverage_is_explicit_in_analysis(service):
    merchant = service.create_merchant(name="京东店", platform="jd", external_shop_id="1")
    service.ingest_orders(merchant["id"], [order()])
    report = service.get_sales_summary(date(2026, 11, 17), date(2026, 11, 17))
    assert report["coverage"]["refunds"]["jd"] == "not_integrated"
    assert any("京东" in warning and "退款" in warning for warning in report["warnings"])


def test_older_snapshot_cannot_roll_completed_order_back(service):
    m = service.create_merchant(name="店", platform="eleme", external_shop_id="1")
    service.ingest_orders(m["id"], [order(updated_at=datetime(2026, 11, 17, 5, tzinfo=UTC))])
    result = service.ingest_orders(
        m["id"], [order(status="accepted", updated_at=datetime(2026, 11, 17, 4, tzinfo=UTC))]
    )
    assert result["skipped"] == 1
    assert (
        service.get_orders(date(2026, 11, 17), date(2026, 11, 17))["items"][0]["status"]
        == "completed"
    )
