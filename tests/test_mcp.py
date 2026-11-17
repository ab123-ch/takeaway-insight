from datetime import UTC, datetime

from mcp.shared.memory import create_connected_server_and_client_session

from takeaway_insight.config import Settings
from takeaway_insight.contracts import NormalizedLine, NormalizedOrder
from takeaway_insight.db import Database
from takeaway_insight.mcp_server import create_mcp_server
from takeaway_insight.services import InsightService


async def test_mcp_protocol_lists_tools_and_calls_sales_service(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'mcp.db'}")
    database.create_schema()
    service = InsightService(database)
    m = service.create_merchant(name="MCP测试门店", platform="eleme", external_shop_id="1")
    service.ingest_orders(
        m["id"],
        [
            NormalizedOrder(
                external_id="A1",
                placed_at=datetime(2026, 11, 17, 1, tzinfo=UTC),
                status="completed",
                items=[NormalizedLine(external_id="L1", name="小炒肉", quantity=3)],
            )
        ],
    )
    server = create_mcp_server(service, Settings(_env_file=None))
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools.tools} >= {
            "list_merchants",
            "get_orders",
            "get_sales_summary",
            "get_product_trend",
            "get_sync_status",
        }
        assert all(t.outputSchema is not None for t in tools.tools)
        result = await client.call_tool(
            "get_sales_summary",
            {"start_date": "2026-11-17", "end_date": "2026-11-17", "merchant_id": m["id"]},
        )
        assert not result.isError
        assert result.structuredContent["summary"]["item_quantity"] == "3"
        merchants = await client.call_tool("list_merchants", {})
        assert merchants.structuredContent["items"][0]["id"] == m["id"]
        assert "configuration" not in merchants.structuredContent["items"][0]
    database.dispose()


async def test_mcp_invalid_date_range_returns_visible_tool_error(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'mcp.db'}")
    database.create_schema()
    server = create_mcp_server(InsightService(database), Settings(_env_file=None))
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        result = await client.call_tool(
            "get_orders", {"start_date": "2026-11-18", "end_date": "2026-11-17"}
        )
        assert result.isError
    database.dispose()
