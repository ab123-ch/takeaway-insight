"""订单原行与拆分组件分层保存，数量使用定点小数。"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """SQLite 不存时区偏移；统一存 UTC 并在读取时恢复时区。"""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("数据库时间必须含时区")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=UTC) if value is not None else None


class Base(DeclarativeBase):
    pass


class Merchant(Base):
    __tablename__ = "merchants"
    __table_args__ = (UniqueConstraint("platform", "external_shop_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    platform: Mapped[str] = mapped_column(String(20))
    external_shop_id: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    sync_interval_minutes: Mapped[int] = mapped_column(Integer, default=15)
    next_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    sync_lock_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sync_lock_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("merchant_id", "external_id"),
        Index("ix_orders_day_merchant", "business_date", "merchant_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.id"))
    external_id: Mapped[str] = mapped_column(String(160))
    serial_no: Mapped[str] = mapped_column(String(120), default="")
    placed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    business_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    refund_status: Mapped[str] = mapped_column(String(20), default="none")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    source_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    synced_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    merchant: Mapped[Merchant] = relationship()
    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderLine.id"
    )


class OrderLine(Base):
    __tablename__ = "order_lines"
    __table_args__ = (UniqueConstraint("order_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(160))
    original_name: Mapped[str] = mapped_column(String(500))
    sku_id: Mapped[str] = mapped_column(String(160), default="")
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    refunded_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    unit: Mapped[str] = mapped_column(String(30), default="份")
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    order: Mapped[Order] = relationship(back_populates="lines")
    components: Mapped[list["OrderComponent"]] = relationship(
        back_populates="line", cascade="all, delete-orphan", order_by="OrderComponent.id"
    )


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("name", "unit"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(500))
    unit: Mapped[str] = mapped_column(String(30), default="份")


class OrderComponent(Base):
    __tablename__ = "order_components"

    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(
        ForeignKey("order_lines.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    refunded_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    source: Mapped[str] = mapped_column(String(30))
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("split_rules.id", ondelete="SET NULL"), nullable=True
    )
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    line: Mapped[OrderLine] = relationship(back_populates="components")
    product: Mapped[Product] = relationship()


class SplitRule(Base):
    __tablename__ = "split_rules"
    __table_args__ = (UniqueConstraint("merchant_id", "match_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.id"))
    match_name: Mapped[str] = mapped_column(String(500))
    components: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (Index("ix_sync_runs_merchant_started", "merchant_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.id"))
    target_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    merchant: Mapped[Merchant] = relationship()
