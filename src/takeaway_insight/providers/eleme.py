"""淘宝闪购/饿了么餐饮 RPC，按官方 Java SDK 1.31.74 的模型适配。"""

import hashlib
import json
import time
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx

from takeaway_insight.contracts import (
    ConfigurationError,
    ConnectionConfig,
    NormalizedComponent,
    NormalizedLine,
    NormalizedOrder,
    ProviderError,
)
from takeaway_insight.providers.common import (
    SHANGHAI,
    HTTPProvider,
    credentials,
    integer_option,
    timestamp,
)

GATEWAYS = {
    "production": "https://open-api.shop.ele.me/api/v1",
    "sandbox": "https://open-api-sandbox.shop.ele.me/api/v1",
}
STATUSES = {
    "pending": "pending",
    "unprocessed": "pending",
    "refunding": "accepted",
    "valid": "accepted",
    "invalid": "cancelled",
    "settled": "completed",
}


def eleme_signature(action: str, token: str, secret: str, metas: dict, params: dict) -> str:
    values = {**params, **metas}
    canonical = "".join(
        key + "=" + json.dumps(values[key], ensure_ascii=False, separators=(",", ":"))
        for key in sorted(values)
        if values[key] is not None
    )
    return hashlib.md5((action + token + canonical + secret).encode()).hexdigest().upper()


class ElemeProvider(HTTPProvider):
    async def rpc(self, action: str, params: dict, config: ConnectionConfig, auth: tuple) -> Any:
        app_key, secret, token = auth
        environment = config.configuration.get("environment", "production")
        if environment not in GATEWAYS:
            raise ConfigurationError("饿了么 environment 必须为 production 或 sandbox")
        milliseconds = time.time_ns() // 1_000_000
        metas = {"app_key": app_key, "timestamp": milliseconds}
        response = await self.post(
            GATEWAYS[environment],
            json={
                "token": token,
                "nop": "1.0.0",
                "metas": metas,
                "params": params,
                "action": action,
                "id": uuid4().hex.upper() + "|" + str(milliseconds),
                "signature": eleme_signature(action, token, secret, metas, params),
            },
        )
        if response.get("error") is not None:
            raise ProviderError(f"饿了么接口 {action} 返回业务错误，请核查权限、授权和调用额度")
        if "result" not in response:
            raise ProviderError(f"饿了么接口 {action} 缺少 result，本次同步未完成")
        return response["result"]

    async def fetch_orders(
        self, connection: ConnectionConfig, target_date: date
    ) -> list[NormalizedOrder]:
        if connection.platform != "eleme":
            raise ConfigurationError("饿了么连接器不能读取其他平台的门店配置")
        auth = credentials(connection)
        if self.client is None:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                return await ElemeProvider(client).fetch_orders(connection, target_date)
        try:
            shop_id = int(connection.external_shop_id)
        except ValueError:
            raise ConfigurationError("饿了么 external_shop_id 必须是平台数字店铺 ID") from None
        page_size = integer_option(connection, "page_size", 50, 50)
        max_pages = integer_option(connection, "max_pages", 100, 100)
        orders: list[NormalizedOrder] = []
        seen: set[str] = set()
        expected_total = None
        try:
            for page in range(1, max_pages + 1):
                result = await self.rpc(
                    "eleme.order.getAllOrders",
                    {
                        "shopId": shop_id,
                        "date": target_date.isoformat(),
                        "pageNo": page,
                        "pageSize": page_size,
                    },
                    connection,
                    auth,
                )
                rows, total = result["list"], result["total"]
                if (
                    not isinstance(rows, list)
                    or isinstance(total, bool)
                    or not isinstance(total, int)
                    or total < 0
                ):
                    raise ValueError("分页格式错误")
                if expected_total is not None and total != expected_total:
                    raise ProviderError("饿了么分页期间订单总数变化，请重新同步以获得完整数据")
                expected_total = total
                for row in rows:
                    if str(row["shopId"]) != str(shop_id):
                        raise ProviderError("饿了么返回了其他门店的订单，本次同步已停止")
                    order = self.normalize(row, connection)
                    if order.placed_at.astimezone(SHANGHAI).date() != target_date:
                        raise ProviderError(
                            "饿了么返回的订单日期与目标营业日不符，请核对查询时间口径"
                        )
                    if order.external_id in seen:
                        raise ProviderError("饿了么分页返回重复订单，本次同步未完成")
                    seen.add(order.external_id)
                    await self.apply_refunds(order, row, connection, auth)
                    orders.append(NormalizedOrder.model_validate(order.model_dump()))
                if len(orders) == total:
                    return orders
                if not rows or len(orders) > total:
                    raise ProviderError("饿了么返回不完整的分页数据，本次同步未完成")
            raise ProviderError("饿了么达到分页上限，无法确认当日订单已完整拉取")
        except (KeyError, ValueError, TypeError, ArithmeticError):
            raise ProviderError("饿了么订单字段不符合已核实的合同，本次同步未完成") from None

    def normalize(self, row: dict, config: ConnectionConfig) -> NormalizedOrder:
        lines = []
        identities = set()
        for group in row["groups"]:
            if group["type"] == "extra":
                continue  # 配送费等不是经营商品。
            if group["type"] not in {"normal", "discount", "combo"}:
                raise ProviderError("饿了么返回未知商品分组类型")
            for item in group["items"]:
                identity = str(item.get("uniqueId") or item["categoryId"])
                if identity in identities:
                    raise ProviderError("饿了么订单包含重复商品行标识")
                identities.add(identity)
                quantity = Decimal(str(item["quantity"]))
                children = item.get("foodGroup") or []
                components = []
                if children:
                    mode = config.configuration.get("bundle_quantity_mode")
                    if mode not in {"per_parent", "order_total"}:
                        raise ConfigurationError(
                            "请核实套餐数量口径并设置 bundle_quantity_mode=per_parent 或 order_total"
                        )
                    for child in children:
                        if child.get("subFoodGroupItem"):
                            raise ProviderError(
                                "饿了么多层套餐数量口径尚未核实，请先配置经核对的商品拆分规则"
                            )
                        count = Decimal(str(child["quantity"]))
                        if mode == "order_total":
                            count /= quantity
                        components.append(
                            NormalizedComponent(
                                name=child["name"],
                                sku_id=str(child.get("skuId") or ""),
                                quantity_per_unit=count,
                            )
                        )
                elif group["type"] == "combo":
                    raise ProviderError(
                        "饿了么旧式 combo 分组缺少明确父子数量关系，需要核对套餐结构"
                    )
                lines.append(
                    NormalizedLine(
                        external_id=identity,
                        name=item["name"],
                        quantity=quantity,
                        sku_id=str(item.get("skuId") or ""),
                        unit_price=Decimal(str(item["price"])),
                        components=components,
                    )
                )
        return NormalizedOrder(
            external_id=str(row["id"]),
            placed_at=timestamp(row["createdAt"]),
            serial_no=str(row.get("daySn") or ""),
            status=STATUSES[row["status"]],
            total_amount=Decimal(str(row["totalPrice"])),
            items=lines,
        )

    async def apply_refunds(
        self, order: NormalizedOrder, row: dict, config: ConnectionConfig, auth: tuple
    ) -> None:
        confirmed_refund_amount = None
        if row.get("refundStatus") not in {
            "noRefund",
            "applied",
            "rejected",
            "arbitrating",
            "failed",
            "successful",
        }:
            raise ProviderError("饿了么返回未知退单状态，本次同步未完成")
        if row.get("refundStatus") == "successful":
            refund = await self.rpc(
                "eleme.order.getRefundOrder", {"orderId": order.external_id}, config, auth
            )
            if str(refund["orderId"]) != order.external_id:
                raise ProviderError("饿了么退单订单号与原订单不符，本次同步已停止")
            if refund["refundStatus"] != "successful":
                raise ProviderError("饿了么退单状态变化，请重新同步")
            if refund.get("refundType") not in {"normal", "part"}:
                raise ProviderError("饿了么退单缺少可识别的 refundType，不能判断全退或部分退款")
            confirmed_refund_amount = Decimal(str(refund["totalPrice"]))
            if confirmed_refund_amount < 0:
                raise ValueError("退款金额不能为负")
            if refund["refundType"] == "normal":
                order.refund_status = "full"
                order.refund_amount = confirmed_refund_amount
                for line in order.items:
                    line.refunded_quantity = line.quantity
                return
        refunds = await self.rpc(
            "eleme.order.queryRefundFoodInfos", {"orderId": order.external_id}, config, auth
        )
        lines = {line.external_id: line for line in order.items}
        seen = set()
        for refund in refunds["goodsList"]:
            count = Decimal(str(refund["refundedUnit"]))
            amount = Decimal(str(refund.get("refundedMoney", 0)))
            if count < 0 or amount < 0:
                raise ValueError("退款数值错误")
            if count > 0:
                identity = str(refund["uniqueId"])
                if identity in seen or identity not in lines or count > lines[identity].quantity:
                    raise ProviderError("饿了么退款商品无法精确匹配原始订单行")
                seen.add(identity)
                lines[identity].refunded_quantity = count
            order.refund_amount += amount
        if order.refund_amount or any(line.refunded_quantity for line in order.items):
            order.refund_status = "partial"
        if confirmed_refund_amount is not None:
            order.refund_status = "partial"
            order.refund_amount = confirmed_refund_amount
