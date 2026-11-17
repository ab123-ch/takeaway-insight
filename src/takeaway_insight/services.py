"""网页、MCP 与同步任务共用的经营分析服务。"""

import os
import re
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import selectinload

from takeaway_insight.contracts import ConnectionConfig, NormalizedOrder, Platform, ProviderError
from takeaway_insight.db import Database
from takeaway_insight.models import (
    Merchant,
    Order,
    OrderComponent,
    OrderLine,
    Product,
    SplitRule,
    SyncRun,
    utcnow,
)
from takeaway_insight.splitting import split_line


def number(value) -> str:
    result = format(Decimal(str(value or 0)), "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def money(value) -> str:
    return format(Decimal(str(value or 0)), ".2f")


class MerchantInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    platform: Platform
    external_shop_id: str = Field(min_length=1, max_length=120)
    timezone: str = "Asia/Shanghai"
    enabled: bool = False
    sync_interval_minutes: int = Field(default=15, ge=1, le=1440)
    configuration: dict[str, str | int] = Field(default_factory=dict)

    @field_validator("name", "external_shop_id")
    @classmethod
    def strip_required(cls, value):
        if not value.strip():
            raise ValueError("名称和门店编号不能为空")
        return value.strip()

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value):
        if value != "Asia/Shanghai":
            raise ValueError("当前两平台仅支持 Asia/Shanghai 时区的中国营业日")
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("无效的门店时区") from error
        return value

    @field_validator("configuration")
    @classmethod
    def env_references_only(cls, value):
        allowed = {
            "app_key_env",
            "app_secret_env",
            "access_token_env",
            "environment",
            "api_mode",
            "page_size",
            "max_pages",
            "bundle_quantity_mode",
        }
        for key, item in value.items():
            if key not in allowed:
                raise ValueError(f"不支持的连接配置字段：{key}；密钥请保存在环境变量")
            if (
                key.endswith("_env")
                and item
                and (not isinstance(item, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", item))
            ):
                raise ValueError(f"{key} 必须填写环境变量名称，不能填写密钥值")
        return value


class RulePartInput(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    factor: Decimal = Field(default=Decimal("1"), gt=0, max_digits=16, decimal_places=4)
    unit: str = Field(default="份", min_length=1, max_length=30)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value):
        if not value.strip():
            raise ValueError("拆分商品名不能为空")
        return value.strip()


class RuleInput(BaseModel):
    merchant_id: int
    match_name: str = Field(min_length=1, max_length=500)
    components: list[RulePartInput] = Field(min_length=1, max_length=50)
    enabled: bool = True


class InsightService:
    def __init__(self, database: Database):
        self.db = database

    @staticmethod
    def _merchant(session, merchant_id: int) -> Merchant:
        merchant = session.get(Merchant, merchant_id)
        if merchant is None:
            raise ValueError("门店不存在")
        return merchant

    @staticmethod
    def _filters(start_date: date, end_date: date, merchant_id=None, platform=None):
        if end_date < start_date:
            raise ValueError("结束日期不能早于开始日期")
        if (end_date - start_date).days > 366:
            raise ValueError("单次分析范围最多367天")
        filters = [Order.business_date >= start_date, Order.business_date <= end_date]
        if merchant_id is not None:
            filters.append(Order.merchant_id == merchant_id)
        if platform is not None:
            if platform not in ("eleme", "jd"):
                raise ValueError("不支持的平台")
            filters.append(Merchant.platform == platform)
        return filters

    @staticmethod
    def _serialize_merchant(m: Merchant, include_configuration: bool = True) -> dict:
        required = ("app_key_env", "app_secret_env", "access_token_env")
        missing = [
            key
            for key in required
            if not m.configuration.get(key) or not os.environ.get(m.configuration[key])
        ]
        data = {
            "id": m.id,
            "name": m.name,
            "platform": m.platform,
            "external_shop_id": m.external_shop_id,
            "timezone": m.timezone,
            "enabled": m.enabled,
            "is_demo": m.is_demo,
            "sync_interval_minutes": m.sync_interval_minutes,
            "connector_status": "演示数据，禁止平台同步"
            if m.is_demo
            else (
                "已实现，待真实门店联调"
                if m.platform == "eleme"
                else "已实现；官方响应说明存在矛盾，待真实门店联调"
            ),
            "configured": not missing,
            "missing_configuration": missing,
            "next_sync_at": m.next_sync_at.isoformat() if m.next_sync_at else None,
        }
        if include_configuration:
            data["configuration"] = m.configuration
        return data

    def list_merchants(self, include_configuration: bool = True) -> list[dict]:
        with self.db.session() as s:
            return [
                self._serialize_merchant(m, include_configuration)
                for m in s.scalars(select(Merchant).order_by(Merchant.id))
            ]

    @staticmethod
    def _coverage(session, merchant_id=None, platform=None):
        stmt = select(Merchant.platform).distinct()
        if merchant_id is not None:
            stmt = stmt.where(Merchant.id == merchant_id)
        if platform is not None:
            stmt = stmt.where(Merchant.platform == platform)
        platforms = set(session.scalars(stmt))
        return {
            "coverage": {
                "refunds": {
                    key: "confirmed_line_refunds" if key == "eleme" else "not_integrated"
                    for key in sorted(platforms)
                }
            },
            "warnings": (
                [
                    "京东秒送尚未接入退款/售后明细；数量是有效状态订单的原始下单量，退款状态未知，金额未完成售后对账，不能当作最终净销量或结算收入。"
                ]
                if "jd" in platforms
                else []
            ),
        }

    def create_merchant(self, **values) -> dict:
        data = MerchantInput.model_validate(values)
        with self.db.session() as s:
            m = Merchant(**data.model_dump())
            s.add(m)
            s.commit()
            return self._serialize_merchant(m)

    def update_merchant(self, merchant_id: int, **values) -> dict:
        data = MerchantInput.model_validate(values)
        with self.db.session() as s:
            self._lock_edit(s, merchant_id)
            m = self._merchant(s, merchant_id)
            if m.sync_lock_token:
                raise ValueError("门店正在同步，请等待完成后修改配置")
            identity_changed = any(
                getattr(m, key) != getattr(data, key)
                for key in ("platform", "external_shop_id", "timezone")
            )
            if (
                identity_changed
                and s.scalar(select(Order.id).where(Order.merchant_id == merchant_id).limit(1))
                is not None
            ):
                raise ValueError("门店已有历史订单，平台、门店编号和时区不可修改，请单独新增门店")
            if m.is_demo and (data.enabled or identity_changed):
                raise ValueError("演示门店不能启用平台同步或修改平台身份，请新增真实门店")
            for key, value in data.model_dump().items():
                setattr(m, key, value)
            s.commit()
            return self._serialize_merchant(m)

    @staticmethod
    def _lock_edit(session, merchant_id):
        # 条件 UPDATE 同时取得 PostgreSQL 行锁 / SQLite 写锁，串行化配置修改、重算与同步租约。
        result = session.execute(
            update(Merchant)
            .where(Merchant.id == merchant_id, Merchant.sync_lock_token.is_(None))
            .values(next_sync_at=Merchant.next_sync_at)
        )
        if result.rowcount != 1:
            if session.get(Merchant, merchant_id) is None:
                raise ValueError("门店不存在")
            raise ValueError("门店正在同步，请等待完成后修改配置或重算")

    def get_connection(self, merchant_id: int) -> ConnectionConfig:
        with self.db.session() as s:
            m = self._merchant(s, merchant_id)
            if m.is_demo:
                raise ValueError("演示门店不可同步，请新增真实平台门店")
            return ConnectionConfig(
                merchant_id=m.id,
                platform=m.platform,
                external_shop_id=m.external_shop_id,
                configuration=m.configuration,
            )

    def list_rules(self, merchant_id: int | None = None) -> list[dict]:
        with self.db.session() as s:
            stmt = select(SplitRule).order_by(SplitRule.id.desc())
            if merchant_id is not None:
                stmt = stmt.where(SplitRule.merchant_id == merchant_id)
            return [
                {
                    "id": r.id,
                    "merchant_id": r.merchant_id,
                    "match_name": r.match_name,
                    "components": r.components,
                    "enabled": r.enabled,
                }
                for r in s.scalars(stmt)
            ]

    def create_rule(self, **values) -> dict:
        data = RuleInput.model_validate(values)
        with self.db.session() as s:
            self._merchant(s, data.merchant_id)
            rule = SplitRule(**data.model_dump(mode="json"))
            s.add(rule)
            s.commit()
            return {"id": rule.id, **data.model_dump(mode="json")}

    def update_rule(self, rule_id: int, **values) -> dict:
        data = RuleInput.model_validate(values)
        with self.db.session() as s:
            rule = s.get(SplitRule, rule_id)
            if rule is None:
                raise ValueError("拆分规则不存在")
            self._merchant(s, data.merchant_id)
            for key, value in data.model_dump(mode="json").items():
                setattr(rule, key, value)
            s.commit()
            return {"id": rule.id, **data.model_dump(mode="json")}

    @staticmethod
    def _rules(session, merchant_id):
        return [
            {
                "id": r.id,
                "match_name": r.match_name,
                "components": r.components,
                "enabled": r.enabled,
            }
            for r in session.scalars(
                select(SplitRule).where(
                    SplitRule.merchant_id == merchant_id, SplitRule.enabled.is_(True)
                )
            )
        ]

    @staticmethod
    def _write_lines(session, row: Order, snapshot: NormalizedOrder, rules):
        row.lines.clear()
        session.flush()
        for source in snapshot.items:
            line = OrderLine(
                external_id=source.external_id,
                original_name=source.name,
                sku_id=source.sku_id,
                quantity=source.quantity,
                refunded_quantity=source.refunded_quantity,
                unit=source.unit,
                unit_price=source.unit_price,
            )
            row.lines.append(line)
            for part in split_line(source, rules):
                product = session.scalar(
                    select(Product).where(Product.name == part.name, Product.unit == part.unit)
                )
                if product is None:
                    insert = sqlite_insert if session.bind.dialect.name == "sqlite" else pg_insert
                    session.execute(
                        insert(Product)
                        .values(name=part.name, unit=part.unit)
                        .on_conflict_do_nothing(index_elements=[Product.name, Product.unit])
                    )
                    product = session.scalar(
                        select(Product).where(Product.name == part.name, Product.unit == part.unit)
                    )
                line.components.append(
                    OrderComponent(
                        product=product,
                        quantity=part.quantity,
                        refunded_quantity=part.refunded_quantity,
                        source=part.source,
                        needs_review=part.needs_review,
                        rule_id=part.rule_id,
                    )
                )

    def ingest_orders(
        self, merchant_id: int, orders: list[NormalizedOrder], lease_token: str | None = None
    ) -> dict:
        result = {"inserted": 0, "updated": 0, "skipped": 0}
        unique: dict[str, NormalizedOrder] = {}
        for incoming in orders:
            previous = unique.get(incoming.external_id)
            if (
                previous is None
                or not previous.updated_at
                or not incoming.updated_at
                or incoming.updated_at >= previous.updated_at
            ):
                unique[incoming.external_id] = incoming
        with self.db.session() as s:
            if lease_token is not None:
                result_lock = s.execute(
                    update(Merchant)
                    .where(
                        Merchant.id == merchant_id,
                        Merchant.sync_lock_token == lease_token,
                        Merchant.sync_lock_until > utcnow(),
                    )
                    .values(sync_lock_token=Merchant.sync_lock_token)
                )
                if result_lock.rowcount != 1:
                    raise ProviderError("同步租约已失效，本次数据未写入")
            merchant = self._merchant(s, merchant_id)
            rules = self._rules(s, merchant_id)
            for original in unique.values():
                snapshot = original.model_copy(deep=True)
                row = s.scalar(
                    select(Order).where(
                        Order.merchant_id == merchant_id, Order.external_id == snapshot.external_id
                    )
                )
                if (
                    row
                    and snapshot.updated_at
                    and row.source_updated_at
                    and snapshot.updated_at < row.source_updated_at
                ):
                    result["skipped"] += 1
                    continue
                if (
                    row
                    and not snapshot.updated_at
                    and row.status in ("completed", "cancelled")
                    and snapshot.status in ("pending", "accepted")
                ):
                    result["skipped"] += 1
                    continue
                if (
                    row
                    and row.status == "cancelled"
                    and snapshot.status != "cancelled"
                    and (
                        not snapshot.updated_at
                        or not row.source_updated_at
                        or snapshot.updated_at <= row.source_updated_at
                    )
                ):
                    result["skipped"] += 1
                    continue
                if row is None:
                    row = Order(merchant_id=merchant_id, external_id=snapshot.external_id)
                    s.add(row)
                    result["inserted"] += 1
                else:
                    result["updated"] += 1
                    # 已确认退款不可被一个没有售后信息的订单快照清零。
                    if row.refund_status != "none" and snapshot.refund_status == "none":
                        snapshot.refund_status = row.refund_status
                        snapshot.refund_amount = row.refund_amount
                    refunded = {line.external_id: line.refunded_quantity for line in row.lines}
                    for item in snapshot.items:
                        item.refunded_quantity = max(
                            item.refunded_quantity, refunded.get(item.external_id, Decimal("0"))
                        )
                        if item.refunded_quantity > item.quantity:
                            raise ValueError(
                                f"订单 {snapshot.external_id} 的已确认退款数量超过调整后商品数量，需要核对平台商品调整记录"
                            )
                row.serial_no = snapshot.serial_no
                row.placed_at = snapshot.placed_at
                row.completed_at = snapshot.completed_at
                row.source_updated_at = snapshot.updated_at
                row.business_date = snapshot.placed_at.astimezone(
                    ZoneInfo(merchant.timezone)
                ).date()
                row.status = snapshot.status
                row.total_amount = snapshot.total_amount
                row.refund_status = snapshot.refund_status
                row.refund_amount = snapshot.refund_amount
                row.source_data = snapshot.model_dump(mode="json")
                row.synced_at = utcnow()
                self._write_lines(s, row, snapshot, rules)
            s.commit()
        return result

    def reprocess_orders(self, merchant_id: int) -> int:
        with self.db.session() as s:
            self._lock_edit(s, merchant_id)
            self._merchant(s, merchant_id)
            rules = self._rules(s, merchant_id)
            rows = list(s.scalars(select(Order).where(Order.merchant_id == merchant_id)))
            for row in rows:
                snapshot = NormalizedOrder.model_validate(row.source_data)
                self._write_lines(s, row, snapshot, rules)
            s.commit()
            return len(rows)

    @staticmethod
    def _net_quantity():
        return case(
            (Order.status.in_(["cancelled", "pending"]), 0),
            (Order.refund_status == "full", 0),
            else_=OrderComponent.quantity - OrderComponent.refunded_quantity,
        )

    def get_sales_summary(
        self,
        start_date: date,
        end_date: date,
        merchant_id: int | None = None,
        platform: str | None = None,
        confirmed_only: bool = False,
    ) -> dict:
        filters = self._filters(start_date, end_date, merchant_id, platform)
        net = self._net_quantity()
        with self.db.session() as s:
            component_filters = filters + (
                [OrderComponent.needs_review.is_(False)] if confirmed_only else []
            )
            rows = s.execute(
                select(
                    Product.id,
                    Product.name,
                    Product.unit,
                    func.sum(OrderComponent.quantity).label("ordered"),
                    func.sum(net).label("sold"),
                    func.sum(OrderComponent.refunded_quantity).label("refunded"),
                    func.sum(case((OrderComponent.needs_review.is_(True), net), else_=0)).label(
                        "review"
                    ),
                    func.count(func.distinct(Order.id)).label("order_count"),
                )
                .select_from(OrderComponent)
                .join(Product)
                .join(OrderLine)
                .join(Order)
                .join(Merchant)
                .where(*component_filters)
                .group_by(Product.id, Product.name, Product.unit)
                .order_by(func.sum(net).desc(), Product.name)
            ).all()
            products = [
                {
                    "product_id": r.id,
                    "name": r.name,
                    "unit": r.unit,
                    "ordered_quantity": number(r.ordered),
                    "sold_quantity": number(r.sold),
                    "refunded_quantity": number(r.refunded),
                    "review_quantity": number(r.review),
                    "order_count": r.order_count,
                }
                for r in rows
            ]
            totals = {}
            for product in products:
                total = totals.setdefault(
                    product["unit"], {"quantity": Decimal("0"), "review_quantity": Decimal("0")}
                )
                total["quantity"] += Decimal(product["sold_quantity"])
                total["review_quantity"] += Decimal(product["review_quantity"])
            quantity_by_unit = [
                {
                    "unit": unit,
                    "quantity": number(values["quantity"]),
                    "review_quantity": number(values["review_quantity"]),
                }
                for unit, values in sorted(totals.items())
            ]
            order_rows = list(s.scalars(select(Order).join(Merchant).where(*filters)))
            active = [o for o in order_rows if o.status in ("accepted", "completed")]
            revenue = sum(
                (max(Decimal("0"), o.total_amount - o.refund_amount) for o in active), Decimal("0")
            )
            trend_rows = s.execute(
                select(Order.business_date, Product.unit, func.sum(net).label("quantity"))
                .select_from(OrderComponent)
                .join(Product)
                .join(OrderLine)
                .join(Order)
                .join(Merchant)
                .where(*component_filters)
                .group_by(Order.business_date, Product.unit)
                .order_by(Order.business_date, Product.unit)
            ).all()
            return {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "summary": {
                    "order_count": len(order_rows),
                    "active_order_count": len(active),
                    "completed_count": sum(o.status == "completed" for o in order_rows),
                    "cancelled_count": sum(o.status == "cancelled" for o in order_rows),
                    "refunded_order_count": sum(
                        o.refund_status in ("partial", "full") for o in order_rows
                    ),
                    "unreconciled_order_count": sum(
                        o.refund_status == "unknown" for o in order_rows
                    ),
                    "item_quantity": quantity_by_unit[0]["quantity"]
                    if len(quantity_by_unit) == 1
                    else ("0" if not quantity_by_unit else None),
                    "review_quantity": quantity_by_unit[0]["review_quantity"]
                    if len(quantity_by_unit) == 1
                    else ("0" if not quantity_by_unit else None),
                    "quantity_by_unit": quantity_by_unit,
                    "revenue": money(revenue),
                    "product_count": len(products),
                },
                "products": products,
                "trend": [
                    {
                        "date": r.business_date.isoformat(),
                        "unit": r.unit,
                        "quantity": number(r.quantity),
                    }
                    for r in trend_rows
                ],
                **self._coverage(s, merchant_id, platform),
                "methodology": "按门店本地时区下单日归属；统计已接单/已完成商品量，扣除已确认退货数量，取消及已确认全退单不计。京东退款尚未对账，详见warnings/coverage。按名称推断的套餐标为待确认；数量不等于实际食材消耗。金额是订单金额扣已知退款，不等于平台结算收入，不向拆分组件均摊。",
            }

    def get_orders(
        self,
        start_date: date,
        end_date: date,
        merchant_id: int | None = None,
        platform: str | None = None,
        status: str | None = None,
        query: str = "",
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError("页码需为正整数，每页最多100条")
        filters = self._filters(start_date, end_date, merchant_id, platform)
        if status:
            if status not in ("pending", "accepted", "completed", "cancelled"):
                raise ValueError("无效的订单状态")
            filters.append(Order.status == status)
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            term = f"%{escaped}%"
            filters.append(
                Order.external_id.ilike(term, escape="\\")
                | Order.serial_no.ilike(term, escape="\\")
                | Order.lines.any(OrderLine.original_name.ilike(term, escape="\\"))
            )
        with self.db.session() as s:
            count = s.scalar(select(func.count(Order.id)).join(Merchant).where(*filters))
            rows = s.scalars(
                select(Order)
                .join(Merchant)
                .where(*filters)
                .options(
                    selectinload(Order.lines)
                    .selectinload(OrderLine.components)
                    .selectinload(OrderComponent.product),
                    selectinload(Order.merchant),
                )
                .order_by(Order.placed_at.desc(), Order.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            items = []
            for o in rows:
                items.append(
                    {
                        "id": o.id,
                        "external_id": o.external_id,
                        "serial_no": o.serial_no,
                        "merchant_id": o.merchant_id,
                        "merchant_name": o.merchant.name,
                        "platform": o.merchant.platform,
                        "business_date": o.business_date.isoformat(),
                        "placed_at": o.placed_at.isoformat(),
                        "status": o.status,
                        "refund_status": o.refund_status,
                        "total_amount": money(o.total_amount),
                        "refund_amount": money(o.refund_amount),
                        "synced_at": o.synced_at.isoformat(),
                        "lines": [
                            {
                                "id": line.id,
                                "external_id": line.external_id,
                                "name": line.original_name,
                                "quantity": number(line.quantity),
                                "refunded_quantity": number(line.refunded_quantity),
                                "unit": line.unit,
                                "sku_id": line.sku_id,
                                "components": [
                                    {
                                        "id": c.id,
                                        "product_id": c.product_id,
                                        "name": c.product.name,
                                        "quantity": number(c.quantity),
                                        "refunded_quantity": number(c.refunded_quantity),
                                        "unit": c.product.unit,
                                        "source": c.source,
                                        "needs_review": c.needs_review,
                                        "rule_id": c.rule_id,
                                    }
                                    for c in line.components
                                ],
                            }
                            for line in o.lines
                        ],
                    }
                )
            return {
                "items": items,
                "total": count,
                "page": page,
                "page_size": page_size,
                **self._coverage(s, merchant_id, platform),
            }

    def get_product_trend(
        self, product_name: str, start_date: date, end_date: date, merchant_id: int | None = None
    ) -> dict:
        filters = self._filters(start_date, end_date, merchant_id)
        with self.db.session() as s:
            rows = s.execute(
                select(
                    Order.business_date,
                    Product.unit,
                    func.sum(self._net_quantity()).label("quantity"),
                )
                .select_from(OrderComponent)
                .join(Product)
                .join(OrderLine)
                .join(Order)
                .join(Merchant)
                .where(*filters, Product.name == product_name)
                .group_by(Order.business_date, Product.unit)
                .order_by(Order.business_date)
            ).all()
            return {
                "product_name": product_name,
                **self._coverage(s, merchant_id),
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "items": [
                    {
                        "date": r.business_date.isoformat(),
                        "unit": r.unit,
                        "quantity": number(r.quantity),
                    }
                    for r in rows
                ],
            }

    def get_sync_status(self, merchant_id: int | None = None, limit: int = 30) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("最多返回100条同步记录")
        with self.db.session() as s:
            stmt = (
                select(SyncRun)
                .options(selectinload(SyncRun.merchant))
                .order_by(SyncRun.id.desc())
                .limit(limit)
            )
            if merchant_id is not None:
                stmt = stmt.where(SyncRun.merchant_id == merchant_id)
            return [
                {
                    "id": r.id,
                    "merchant_id": r.merchant_id,
                    "merchant_name": r.merchant.name,
                    "platform": r.merchant.platform,
                    "target_date": r.target_date.isoformat(),
                    "status": r.status,
                    "started_at": r.started_at.isoformat(),
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                    "fetched": r.fetched,
                    "inserted": r.inserted,
                    "updated": r.updated,
                    "error": r.error,
                }
                for r in s.scalars(stmt)
            ]
