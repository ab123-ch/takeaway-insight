"""供本地Agent技能直接调用的数据库工具，不设置SQL只读或表白名单。"""

import argparse
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import inspect, text

from takeaway_insight.contracts import NormalizedOrder
from takeaway_insight.services import InsightService, number
from takeaway_insight.splitting import split_line


class DatabaseSkill:
    def __init__(self, database):
        self.db = database
        self.service = InsightService(database)

    def inspect_database(self):
        inspector = inspect(self.db.engine)
        return {
            "backend": self.db.engine.dialect.name,
            "tables": {
                table: {
                    "columns": [
                        {"name": col["name"], "type": str(col["type"]), "nullable": col["nullable"]}
                        for col in inspector.get_columns(table)
                    ],
                    "primary_key": inspector.get_pk_constraint(table),
                    "foreign_keys": inspector.get_foreign_keys(table),
                }
                for table in inspector.get_table_names()
            },
        }

    def sql(self, statement, parameters=None):
        with self.db.engine.begin() as connection:
            result = connection.execute(text(statement), parameters or {})
            return {
                "rowcount": result.rowcount,
                "rows": [dict(row) for row in result.mappings()] if result.returns_rows else [],
            }

    def ingest(self, merchant_id, rows):
        return self.service.ingest_orders(
            merchant_id, [NormalizedOrder.model_validate(row) for row in rows]
        )

    def reconcile(self, merchant_id, target_date, rows):
        self.service.get_connection(merchant_id)
        expected = [NormalizedOrder.model_validate(row) for row in rows]
        expected_by_id = {order.external_id: order for order in expected}
        actual = {}
        page = 1
        while True:
            response = self.service.get_orders(
                target_date, target_date, merchant_id=merchant_id, page=page, page_size=100
            )
            actual.update({row["external_id"]: row for row in response["items"]})
            if page * 100 >= response["total"]:
                break
            page += 1
        rules = self.service.list_rules(merchant_id)
        differences = []
        for source in expected:
            duplicated_lines = sorted(
                key
                for key, count in Counter(line.external_id for line in source.items).items()
                if count > 1
            )
            if duplicated_lines:
                differences.append(
                    {
                        "order_id": source.external_id,
                        "field": "duplicate_line_ids",
                        "line_ids": duplicated_lines,
                    }
                )
        for identity in sorted(expected_by_id.keys() & actual.keys()):
            source, stored = expected_by_id[identity], actual[identity]
            for key in (
                "serial_no",
                "total_amount",
                "refund_amount",
                "placed_at",
                "completed_at",
                "updated_at",
            ):
                wanted_value, saved_value = getattr(source, key), stored[key]
                if key in ("total_amount", "refund_amount"):
                    wanted_value, saved_value = number(wanted_value), number(saved_value)
                elif key.endswith("_at") and saved_value:
                    saved_value = datetime.fromisoformat(saved_value)
                if wanted_value != saved_value:
                    differences.append({"order_id": identity, "field": key})
            if source.status != stored["status"] or source.refund_status != stored["refund_status"]:
                differences.append({"order_id": identity, "field": "order_status_or_refund"})
            source_lines = {line.external_id: line for line in source.items}
            saved_lines = {line["external_id"]: line for line in stored["lines"]}
            if source_lines.keys() != saved_lines.keys():
                differences.append({"order_id": identity, "field": "line_ids"})
            for line_id in source_lines.keys() & saved_lines.keys():
                line, saved = source_lines[line_id], saved_lines[line_id]
                if (
                    line.name,
                    number(line.quantity),
                    number(line.refunded_quantity),
                    line.unit,
                    line.sku_id,
                    number(line.unit_price) if line.unit_price is not None else None,
                ) != (
                    saved["name"],
                    saved["quantity"],
                    saved["refunded_quantity"],
                    saved["unit"],
                    saved["sku_id"],
                    number(saved["unit_price"]) if saved["unit_price"] is not None else None,
                ):
                    differences.append(
                        {"order_id": identity, "line_id": line_id, "field": "original_line"}
                    )
                wanted = Counter(
                    (part.name, part.unit, number(part.quantity), number(part.refunded_quantity))
                    for part in split_line(line, rules)
                )
                got = Counter(
                    (part["name"], part["unit"], part["quantity"], part["refunded_quantity"])
                    for part in saved["components"]
                )
                if wanted != got:
                    differences.append(
                        {"order_id": identity, "line_id": line_id, "field": "components"}
                    )
        missing = sorted(expected_by_id.keys() - actual.keys())
        extra = sorted(actual.keys() - expected_by_id.keys())
        duplicates = sorted(
            key
            for key, count in Counter(order.external_id for order in expected).items()
            if count > 1
        )
        return {
            "ok": not (missing or extra or differences or duplicates),
            "merchant_id": merchant_id,
            "business_date": target_date.isoformat(),
            "expected_order_count": len(expected),
            "stored_order_count": len(actual),
            "missing_order_ids": missing,
            "extra_order_ids": extra,
            "duplicate_expected_ids": duplicates,
            "differences": differences,
        }


def main():
    from takeaway_insight.cli import load_runtime
    from takeaway_insight.db import Database

    parser = argparse.ArgumentParser(description="餐账本地数据库技能")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect")
    query = commands.add_parser("sql")
    query.add_argument("statement")
    query.add_argument("--params", default="{}")
    for name in ("ingest", "reconcile"):
        command = commands.add_parser(name)
        command.add_argument("--merchant", type=int, required=True)
        command.add_argument("--file", type=Path, required=True)
        if name == "reconcile":
            command.add_argument("--date", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    db = Database(load_runtime().database_url)
    try:
        tool = DatabaseSkill(db)
        if args.command == "inspect":
            result = tool.inspect_database()
        elif args.command == "sql":
            result = tool.sql(args.statement, json.loads(args.params))
        else:
            payload = json.loads(args.file.read_text())
            rows = payload["orders"] if isinstance(payload, dict) else payload
            result = (
                tool.ingest(args.merchant, rows)
                if args.command == "ingest"
                else tool.reconcile(args.merchant, args.date, rows)
            )
        print(json.dumps(result, ensure_ascii=False, default=str))
        if args.command == "reconcile" and not result["ok"]:
            raise SystemExit(1)
    finally:
        db.dispose()
