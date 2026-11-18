"""标准 MCP 数据服务，供 dsh 等客户端查询经营数据。"""

from datetime import date
from functools import partial
from typing import Any
from urllib.parse import urlsplit

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from takeaway_insight.config import Settings
from takeaway_insight.services import InsightService


def create_mcp_server(service: InsightService, settings: Settings) -> FastMCP:
    public = urlsplit(settings.public_url)
    server = FastMCP(
        "餐账",
        instructions="查询授权门店的外卖销量。先读取门店和同步记录，注意数据新鲜度、套餐needs_review标记以及warnings/coverage。京东尚未接入退款售后，refund_status=unknown，不得将其原始订单数量当成退款后净销量。所有时间范围按门店营业日期，不包含顾客个人信息。销量不是实际原材料消耗；备货分析还需配方、库存和供货时间。",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            allowed_hosts=["localhost:*", "127.0.0.1:*", "[::1]:*", public.netloc],
            allowed_origins=[
                "http://localhost:*",
                "http://127.0.0.1:*",
                settings.public_url.rstrip("/"),
            ],
        ),
    )

    @server.tool(structured_output=True)
    async def list_merchants() -> dict[str, Any]:
        """列出门店ID、平台、时区和同步配置状态，不返回密钥及配置引用。"""
        rows = await anyio.to_thread.run_sync(
            partial(service.list_merchants, include_configuration=False)
        )
        return {"items": rows}

    @server.tool(structured_output=True)
    async def get_sales_summary(
        start_date: str,
        end_date: str,
        merchant_id: int | None = None,
        platform: str | None = None,
        confirmed_only: bool = False,
    ) -> dict[str, Any]:
        """按YYYY-MM-DD日期范围查询商品销量、订单数、趋势与待确认数量。最多367天；商品份数和金额均为十进制字符串。"""
        return await anyio.to_thread.run_sync(
            partial(
                service.get_sales_summary,
                date.fromisoformat(start_date),
                date.fromisoformat(end_date),
                merchant_id,
                platform,
                confirmed_only,
            )
        )

    @server.tool(structured_output=True)
    async def get_orders(
        start_date: str,
        end_date: str,
        merchant_id: int | None = None,
        platform: str | None = None,
        status: str | None = None,
        query: str = "",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """分页读取原始订单商品行及拆分子项，保留同一订单号。page_size最多100，返回total便于继续翻页。"""
        return await anyio.to_thread.run_sync(
            partial(
                service.get_orders,
                date.fromisoformat(start_date),
                date.fromisoformat(end_date),
                merchant_id,
                platform,
                status,
                query,
                page,
                page_size,
            )
        )

    @server.tool(structured_output=True)
    async def get_product_trend(
        product_name: str, start_date: str, end_date: str, merchant_id: int | None = None
    ) -> dict[str, Any]:
        """读取指定商品按日销量。同名不同单位分开返回，不擅自换算原材料。"""
        return await anyio.to_thread.run_sync(
            partial(
                service.get_product_trend,
                product_name,
                date.fromisoformat(start_date),
                date.fromisoformat(end_date),
                merchant_id,
            )
        )

    @server.tool(structured_output=True)
    async def get_sync_status(merchant_id: int | None = None, limit: int = 30) -> dict[str, Any]:
        """查询最近同步结果和失败原因。分析前检查是否有缺失、失败或仍在进行中的同步，最多100条。"""
        return {
            "items": await anyio.to_thread.run_sync(
                partial(service.get_sync_status, merchant_id, limit)
            )
        }

    return server


def main() -> None:
    from takeaway_insight.cli import load_runtime
    from takeaway_insight.database_migrations import upgrade_database
    from takeaway_insight.db import Database

    settings = load_runtime()
    database = Database(settings.database_url)
    upgrade_database(database)
    try:
        create_mcp_server(InsightService(database), settings).run(transport="stdio")
    finally:
        database.dispose()
