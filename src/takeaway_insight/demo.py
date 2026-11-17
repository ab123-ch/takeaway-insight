"""只由显式 demo 命令调用；演示门店禁止发起平台同步。"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from takeaway_insight.contracts import NormalizedComponent, NormalizedLine, NormalizedOrder
from takeaway_insight.models import Merchant


def seed_demo(service, today: date | None = None):
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    for platform, name in (
        ("eleme", "演示 · 巷口烧味（饿了么）"),
        ("jd", "演示 · 巷口烧味（京东秒送）"),
    ):
        with service.db.session() as session:
            merchant = session.scalar(
                select(Merchant).where(
                    Merchant.platform == platform, Merchant.external_shop_id == "DEMO-" + platform
                )
            )
            if merchant is None:
                merchant = Merchant(
                    name=name,
                    platform=platform,
                    external_shop_id="DEMO-" + platform,
                    is_demo=True,
                    enabled=False,
                )
                session.add(merchant)
                session.commit()
            merchant_id = merchant.id
        orders = []
        for day_offset in range(8):
            day = today - timedelta(days=day_offset)
            for index in range(7 if platform == "eleme" else 4):
                combo = index % 3 == 0
                line = NormalizedLine(
                    external_id="1",
                    name="半只鸡+半只鸭" if combo else ("招牌烧鸭饭" if index % 2 else "豉油鸡饭"),
                    quantity=1 + index % 2,
                    unit_price="38.00" if combo else "22.00",
                )
                if combo and platform == "jd":
                    line.components = [
                        NormalizedComponent(name="半只鸡"),
                        NormalizedComponent(name="半只鸭"),
                    ]
                cancelled = index == 6
                refunded = index == 5
                if refunded:
                    line.refunded_quantity = line.quantity
                orders.append(
                    NormalizedOrder(
                        external_id=f"DEMO-{platform}-{day}-{index + 1:03}",
                        serial_no=f"#{index + 1:03}",
                        placed_at=datetime.combine(
                            day, time(11 + index, 12), ZoneInfo("Asia/Shanghai")
                        ),
                        status="cancelled" if cancelled else "completed",
                        total_amount=Decimal("38" if combo else "22") * line.quantity,
                        refund_status="full" if refunded else "none",
                        refund_amount=Decimal("22") * line.quantity if refunded else 0,
                        items=[line],
                    )
                )
        service.ingest_orders(merchant_id, orders)
    return {"message": "已导入明确标记的演示门店与订单；演示门店不能同步平台数据"}
