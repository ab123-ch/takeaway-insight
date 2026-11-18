import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from takeaway_insight.config import Settings
from takeaway_insight.db import Database
from takeaway_insight.web import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/web.db",
        secret_key="s" * 40,
        admin_password="test-password-only",
        mcp_token="m" * 40,
    )
    db = Database(settings.database_url)
    db.create_schema()
    with TestClient(create_app(settings, db), base_url="http://127.0.0.1:8765") as client:
        yield client
    db.dispose()


def login(client):
    response = client.get("/login")
    token = re.search('name="csrf_token" value="([^"]+)"', response.text).group(1)
    response = client.post(
        "/login", data={"username": "admin", "password": "test-password-only", "csrf_token": token}
    )
    assert response.status_code == 200
    return client.get("/api/session").json()["csrf_token"]


def test_web_requires_login_and_csrf_for_mutations(client):
    assert client.get("/api/merchants").status_code == 401
    csrf = login(client)
    payload = {"name": "门店A", "platform": "eleme", "external_shop_id": "100"}
    assert client.post("/api/merchants", json=payload).status_code == 403
    result = client.post("/api/merchants", json=payload, headers={"X-CSRF-Token": csrf})
    assert result.status_code == 201
    assert client.get("/api/merchants").json()["items"][0]["name"] == "门店A"
    assert (
        client.post("/api/merchants", json=payload, headers={"X-CSRF-Token": csrf}).status_code
        == 409
    )


def test_bad_login_and_bad_date_range_visible(client):
    assert client.post("/login", data={"username": "admin", "password": "wrong"}).status_code == 403
    login(client)
    assert client.get("/api/sales?start_date=2026-11-17&end_date=2026-11-16").status_code == 400
    assert (
        client.get("/api/sales?start_date=2026-11-17&end_date=2026-11-17").json()["summary"][
            "order_count"
        ]
        == 0
    )
    assert client.get(
        "/api/export/sales.csv?start_date=2026-11-17&end_date=2026-11-17"
    ).content.startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_http_mcp_uses_dedicated_bearer_and_real_protocol(client, path):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream"}
    assert client.post(path, json=payload, headers=headers).status_code == 401
    login(client)
    assert client.post(path, json=payload, headers=headers).status_code == 401
    response = client.post(
        path, json=payload, headers={**headers, "Authorization": "Bearer " + "m" * 40}
    )
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "餐账"


def test_unsafe_csv_cells_are_escaped():
    from takeaway_insight.web import csv_cell

    assert csv_cell("=SUM(A1)") == "'=SUM(A1)"
    assert csv_cell("半只鸡") == "半只鸡"


def test_agent_task_routes_create_queue_and_show_logs(client):
    import sys

    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    payload = {
        "name": "自动采集",
        "runner": "custom",
        "command": [sys.executable, "-c", "print('完成')"],
        "prompt": "由Agent自行入库核对 {{ today }}",
        "enabled": True,
        "interval_minutes": 15,
    }
    response = client.post("/api/agent-tasks", json=payload, headers=headers)
    assert response.status_code == 201
    task_id = response.json()["id"]
    assert client.get("/agents").status_code == 200
    run = client.post(
        f"/api/agent-tasks/{task_id}/run", json={"request": "采集今天"}, headers=headers
    )
    assert run.status_code == 202
    assert run.json()["status"] == "queued"
    assert "采集今天" in client.get("/api/agent-runs/" + run.json()["id"]).json()["prompt"]
    assert "等待worker" in client.get("/api/agent-runs/" + run.json()["id"] + "/log").text


def test_csv_carries_unknown_jd_refund_coverage(client):
    from takeaway_insight.contracts import NormalizedLine, NormalizedOrder

    login(client)
    service = client.app.state.service
    merchant = service.create_merchant(name="JD", platform="jd", external_shop_id="1")
    service.ingest_orders(
        merchant["id"],
        [
            NormalizedOrder(
                external_id="1",
                status="completed",
                refund_status="unknown",
                placed_at=datetime(2026, 11, 17, 3, tzinfo=UTC),
                items=[NormalizedLine(external_id="1", name="鸡", quantity=2)],
            )
        ],
    )
    text = client.get("/api/export/sales.csv?start_date=2026-11-17&end_date=2026-11-17").text
    assert "售后" in text and "未" in text and "京东" in text


def test_demo_sync_is_rejected_before_scheduling_background_job(client):
    from takeaway_insight.demo import seed_demo

    csrf = login(client)
    seed_demo(client.app.state.service)
    response = client.post(
        "/api/sync",
        json={"merchant_id": 1, "start_date": "2026-11-17", "end_date": "2026-11-17"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 400
    assert "演示" in response.json()["detail"]


def test_today_uses_business_timezone_instead_of_server_clock(client, monkeypatch):
    import takeaway_insight.web as web

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2027, 11, 16, 17, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(web, "datetime", FixedDateTime, raising=False)
    login(client)
    assert client.get("/api/session").json()["today"] == "2027-11-17"
