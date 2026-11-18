from datetime import date

import pytest

from takeaway_insight.database_skill import DatabaseSkill
from takeaway_insight.db import Database
from takeaway_insight.services import InsightService


@pytest.fixture
def skill(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/skill.db")
    db.create_schema()
    merchant = InsightService(db).create_merchant(
        name="烧味店", platform="meituan", external_shop_id="shop-1"
    )
    yield DatabaseSkill(db), merchant["id"]
    db.dispose()


def sample():
    return [
        {
            "external_id": "order-1",
            "placed_at": "2026-11-18T12:00:00+08:00",
            "status": "completed",
            "total_amount": "76",
            "items": [{"external_id": "line-1", "name": "半只鸡+半只鸭", "quantity": "2"}],
        }
    ]


def test_skill_accepts_write_sql_and_inspects_actual_tables(skill):
    tool, merchant_id = skill
    assert "orders" in tool.inspect_database()["tables"]
    tool.sql("CREATE TABLE agent_probe (value TEXT)")
    tool.sql("INSERT INTO agent_probe(value) VALUES (:value)", {"value": "可写"})
    assert tool.sql("SELECT value FROM agent_probe")["rows"] == [{"value": "可写"}]


def test_skill_ingests_combo_then_reconciles_saved_data(skill):
    tool, merchant_id = skill
    assert tool.ingest(merchant_id, sample())["inserted"] == 1
    assert tool.ingest(merchant_id, sample())["updated"] == 1
    result = tool.reconcile(merchant_id, date(2026, 11, 18), sample())
    assert result["ok"] and result["stored_order_count"] == 1
    rows = tool.service.get_orders(date(2026, 11, 18), date(2026, 11, 18), platform="meituan")
    assert len(rows["items"][0]["lines"][0]["components"]) == 2
    changed = sample()
    changed[0]["items"][0]["quantity"] = "3"
    assert not tool.reconcile(merchant_id, date(2026, 11, 18), changed)["ok"]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE orders SET serial_no='wrong'",
        "UPDATE orders SET placed_at='2026-11-18 12:30:00'",
        "UPDATE orders SET total_amount=999",
        "UPDATE orders SET refund_amount=10",
        "UPDATE order_lines SET sku_id='wrong'",
        "UPDATE order_lines SET unit_price=5",
    ],
)
def test_reconcile_detects_changes_in_saved_business_fields(skill, statement):
    tool, merchant_id = skill
    tool.ingest(merchant_id, sample())
    tool.sql(statement)
    assert not tool.reconcile(merchant_id, date(2026, 11, 18), sample())["ok"]


def test_reconcile_rejects_nonexistent_merchant(skill):
    tool, _ = skill
    with pytest.raises(ValueError, match="门店不存在"):
        tool.reconcile(999, date(2026, 11, 18), [])


def test_reconcile_detects_duplicate_source_line_ids_instead_of_collapsing_quantities(skill):
    tool, merchant_id = skill
    rows = sample()
    tool.ingest(merchant_id, rows)
    rows[0]["items"].append(dict(rows[0]["items"][0]))
    report = tool.reconcile(merchant_id, date(2026, 11, 18), rows)
    assert not report["ok"]
    assert any(diff["field"] == "duplicate_line_ids" for diff in report["differences"])
