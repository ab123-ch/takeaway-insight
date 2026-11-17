"""京东秒送协议必须区分已迁移的新平台与旧平台。"""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal

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
    json_value,
    timestamp,
)

GATEWAY = "https://openapi.jddj.com/djapi/ApiPlatformService/querySellerOrderPage"
STATUSES = {
    20010: "pending",
    20020: "cancelled",
    20030: "accepted",
    20040: "cancelled",
    31000: "pending",
    31020: "accepted",
    41000: "pending",
    32000: "accepted",
    33040: "accepted",
    33060: "completed",
    90000: "completed",
}
RETURNED_FIELDS = [
    "orderId",
    "deliveryStationNo",
    "orderStartTime",
    "orderStatusTime",
    "orderStatus",
    "orderNum",
    "orderBuyerPayableMoney",
    "deliveryConfirmTime",
    "product",
    "adjustIsExists",
]


def jd_signature(params: dict[str, str], secret: str) -> str:
    canonical = secret + "".join(key + params[key] for key in sorted(params)) + secret
    return hashlib.md5(canonical.encode()).hexdigest().upper()


class JDProvider(HTTPProvider):
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        super().__init__(client)
        self.clock = clock or (lambda: datetime.now(SHANGHAI))
        self.sleeper = sleeper
        self.last_timestamp: datetime | None = None

    async def next_timestamp(self) -> str:
        now = self.clock().astimezone(SHANGHAI)
        if self.last_timestamp is not None and now.replace(microsecond=0) <= self.last_timestamp:
            delay = (self.last_timestamp + timedelta(seconds=1) - now).total_seconds()
            if delay > 2:
                raise ProviderError("京东请求期间系统时钟回退，请校准时间后重试")
            await self.sleeper(delay)
            now = self.clock().astimezone(SHANGHAI)
            if now.replace(microsecond=0) <= self.last_timestamp:
                raise ProviderError("京东请求时间未推进到下一秒，本次同步已停止")
        self.last_timestamp = now.replace(microsecond=0)
        return self.last_timestamp.strftime("%Y-%m-%d %H:%M:%S")

    async def fetch_orders(
        self, connection: ConnectionConfig, target_date: date
    ) -> list[NormalizedOrder]:
        if connection.platform != "jd":
            raise ConfigurationError("京东连接器不能读取其他平台的门店配置")
        mode = connection.configuration.get("api_mode", "new")
        if mode == "legacy":
            raise ConfigurationError("京东旧平台不自动兼容，请迁移并配置已获批的新版订单接口")
        if mode != "new":
            raise ConfigurationError("京东 api_mode 必须明确使用 new；旧平台不自动降级")
        environment = connection.configuration.get("environment", "production")
        if environment != "production":
            raise ConfigurationError("京东新版当前仅核实 production 网关，不自动替换沙箱地址")
        app_key, secret, token = credentials(connection)
        if self.client is None:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                return await JDProvider(
                    client, clock=self.clock, sleeper=self.sleeper
                ).fetch_orders(connection, target_date)
        page_size = integer_option(connection, "page_size", 100, 100)
        max_pages = integer_option(connection, "max_pages", 99, 99)
        orders: list[NormalizedOrder] = []
        seen = set()
        day_start = datetime.combine(target_date, time.min, tzinfo=SHANGHAI)
        try:
            for hour in range(24):
                start = day_start + timedelta(hours=hour)
                end = start + timedelta(hours=1) - timedelta(seconds=1)
                for page in range(1, max_pages + 1):
                    business = {
                        "deliveryStationNo": connection.external_shop_id,
                        "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
                        "endTime": end.strftime("%Y-%m-%d %H:%M:%S"),
                        "dateType": 1,
                        "sortOrder": "ASC",
                        "page": page,
                        "pageSize": page_size,
                        "returnedFields": RETURNED_FIELDS,
                    }
                    form = {
                        "app_key": app_key,
                        "token": token,
                        "format": "json",
                        "v": "1.0",
                        "timestamp": await self.next_timestamp(),
                        "jd_param_json": json.dumps(
                            business, ensure_ascii=False, separators=(",", ":")
                        ),
                    }
                    form["sign"] = jd_signature(form, secret)
                    response = await self.post(GATEWAY, data=form)
                    if str(response.get("code")) != "0":
                        raise ProviderError("京东订单接口返回业务错误，请核查应用权限和门店授权")
                    if response.get("encryptData"):
                        raise ProviderError(
                            "京东返回 encryptData，加密响应尚待官方合同联调，本次同步未完成"
                        )
                    data = json_value(response["data"])
                    if str(data.get("code")) != "0":
                        raise ProviderError("京东订单查询返回内部业务错误，本次同步未完成")
                    result = json_value(data["result"])
                    if not isinstance(result, dict) or not isinstance(
                        result.get("resultList"), list
                    ):
                        raise ProviderError(
                            "京东分页响应缺少 data.result.resultList，公开文档存在矛盾，需要实店联调"
                        )
                    rows = result["resultList"]
                    if len(rows) > page_size:
                        raise ProviderError("京东返回超过请求 pageSize 的数据，无法确认分页完整性")
                    for row in rows:
                        if str(row["deliveryStationNo"]) != connection.external_shop_id:
                            raise ProviderError("京东返回了其他门店的订单，本次同步已停止")
                        order = self.normalize(row, connection)
                        if order.placed_at.astimezone(SHANGHAI).date() != target_date:
                            raise ProviderError("京东返回的订单日期与目标营业日不符")
                        if (
                            not start
                            <= order.placed_at.astimezone(SHANGHAI)
                            < start + timedelta(hours=1)
                        ):
                            raise ProviderError(
                                "京东返回的订单不在请求小时窗口，无法确认分页完整性"
                            )
                        if order.external_id in seen:
                            raise ProviderError("京东分页或小时窗口返回重复订单，本次同步未完成")
                        seen.add(order.external_id)
                        orders.append(order)
                    if len(rows) < page_size:
                        break
                else:
                    raise ProviderError("京东达到分页上限，无法确认当日订单已完整拉取")
            return orders
        except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError):
            raise ProviderError(
                "京东订单字段不符合当前连接器合同，需要实店联调，本次同步未完成"
            ) from None

    def normalize(self, row: dict, config: ConnectionConfig) -> NormalizedOrder:
        if row.get("adjustIsExists"):
            raise ProviderError("京东订单存在商品调整，数量对账合同尚待核实")
        lines = []
        for product in row["product"]:
            if product.get("adjustMode", 0) != 0 or product.get("adjustId", 0) != 0:
                raise ProviderError("京东商品存在调整记录，不能直接累计原始下单数量")
            quantity = Decimal(str(product["skuCount"]))
            components = []
            children = json_value(product.get("productSubInfo") or [])
            if children:
                if not isinstance(children, list):
                    raise ProviderError("京东套餐 productSubInfo 不是 JSON 数组")
                mode = config.configuration.get("bundle_quantity_mode")
                if mode not in {"per_parent", "order_total"}:
                    raise ConfigurationError(
                        "请核实京东套餐数量口径并设置 bundle_quantity_mode=per_parent 或 order_total"
                    )
                for child in children:
                    count = Decimal(str(child["skuCount"]))
                    if mode == "order_total":
                        count /= quantity
                    components.append(
                        NormalizedComponent(
                            name=child["skuName"],
                            sku_id=str(child["skuId"]),
                            quantity_per_unit=count,
                        )
                    )
            if not product.get("supplyName"):
                raise ProviderError("京东商品缺少秒送商品 UUID（supplyName），无法确认订单行标识")
            lines.append(
                NormalizedLine(
                    external_id=str(product["supplyName"]),
                    name=product["skuName"],
                    sku_id=str(product["skuId"]),
                    quantity=quantity,
                    unit_price=Decimal(str(product["skuJdPrice"])) / 100,
                    components=components,
                )
            )
        return NormalizedOrder(
            refund_status="unknown",
            external_id=str(row["orderId"]),
            placed_at=timestamp(row["orderStartTime"]),
            status=STATUSES[int(row["orderStatus"])],
            serial_no=str(row.get("orderNum") or ""),
            total_amount=Decimal(str(row["orderBuyerPayableMoney"])) / 100,
            items=lines,
            updated_at=timestamp(row["orderStatusTime"]) if row.get("orderStatusTime") else None,
            completed_at=timestamp(row["deliveryConfirmTime"])
            if row.get("deliveryConfirmTime")
            else None,
        )
