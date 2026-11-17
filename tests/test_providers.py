"""仅使用人工构造的协议样例；不代表平台生产联调已通过。"""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest

from takeaway_insight.contracts import ConfigurationError, ConnectionConfig, ProviderError
from takeaway_insight.providers import get_provider
from takeaway_insight.providers.eleme import ElemeProvider, eleme_signature
from takeaway_insight.providers.jd import JDProvider, jd_signature


@pytest.fixture
def connection(monkeypatch):
    for name, value in [("SHOP_KEY", "key"), ("SHOP_SECRET", "secret"), ("SHOP_TOKEN", "token")]:
        monkeypatch.setenv(name, value)
    return ConnectionConfig(
        merchant_id=1,
        platform="eleme",
        external_shop_id="123",
        configuration={
            "app_key_env": "SHOP_KEY",
            "app_secret_env": "SHOP_SECRET",
            "access_token_env": "SHOP_TOKEN",
            "page_size": 1,
        },
    )


def raw_order(order_id="order-1", **changes):
    return {
        "id": order_id,
        "shopId": 123,
        "createdAt": "2026-11-17T12:30:00+08:00",
        "daySn": 6,
        "status": "settled",
        "refundStatus": "noRefund",
        "totalPrice": "39.80",
        "groups": [
            {
                "type": "normal",
                "items": [
                    {
                        "id": "food-1",
                        "uniqueId": "line-1",
                        "skuId": "sku-1",
                        "name": "半只鸡+半只鸭",
                        "quantity": 2,
                        "price": "19.90",
                    }
                ],
            }
        ],
        "consignee": "不得保存",
        "phoneList": "不得保存",
        **changes,
    }


def rpc_response(result):
    return httpx.Response(200, json={"result": result, "error": None})


def test_signatures_use_json_values_and_pre_encoding_strings():
    params = {"date": "2026-11-17", "shopId": 123, "pageNo": 1, "pageSize": 50}
    metas = {"app_key": "key", "timestamp": 1700000000000}
    canonical = 'eleme.order.getAllOrderstokenapp_key="key"date="2026-11-17"pageNo=1pageSize=50shopId=123timestamp=1700000000000secret'
    assert (
        eleme_signature("eleme.order.getAllOrders", "token", "secret", metas, params)
        == hashlib.md5(canonical.encode()).hexdigest().upper()
    )
    assert (
        jd_signature({"z": "中文 空格", "a": '{"name":"鸡"}'}, "secret")
        == hashlib.md5('secreta{"name":"鸡"}z中文 空格secret'.encode()).hexdigest().upper()
    )


async def test_eleme_pages_refunds_utc_and_privacy(connection):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if body["action"] == "eleme.order.queryRefundFoodInfos":
            return rpc_response(
                {"goodsList": [{"uniqueId": "line-1", "refundedUnit": 1, "refundedMoney": "19.90"}]}
            )
        page = body["params"]["pageNo"]
        return rpc_response({"list": [raw_order(f"order-{page}")], "total": 2})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        orders = await ElemeProvider(client).fetch_orders(connection, date(2026, 11, 17))
    assert len(orders) == 2
    assert orders[0].placed_at.tzinfo is UTC
    assert orders[0].placed_at.hour == 4
    assert orders[0].items[0].quantity == Decimal("2")
    assert orders[0].items[0].refunded_quantity == Decimal("1")
    assert orders[0].refund_status == "partial"
    assert orders[0].refund_amount == Decimal("19.90")
    assert "不得保存" not in orders[0].model_dump_json()
    assert seen[0]["params"] == {"shopId": 123, "date": "2026-11-17", "pageNo": 1, "pageSize": 1}
    assert seen[0]["metas"]["timestamp"] > 10**12


async def test_missing_config_never_uses_other_environment_variables(connection, monkeypatch):
    monkeypatch.delenv("SHOP_TOKEN")
    monkeypatch.setenv("ELEME_ACCESS_TOKEN", "wrong-shop-token")
    with pytest.raises(ConfigurationError, match="SHOP_TOKEN"):
        await ElemeProvider().fetch_orders(connection, date(2026, 11, 17))


@pytest.mark.parametrize("failure", ["business", "http", "malformed", "wrong_shop", "duplicate"])
async def test_incomplete_eleme_pages_fail_entire_fetch(connection, failure):
    def handler(request):
        body = json.loads(request.content)
        if body["action"] == "eleme.order.queryRefundFoodInfos":
            return rpc_response({"goodsList": []})
        page = body["params"]["pageNo"]
        if page == 1:
            return rpc_response({"list": [raw_order()], "total": 2})
        if failure == "business":
            return httpx.Response(
                200, json={"error": {"code": "ACCESS_DENIED", "message": "secret token"}}
            )
        if failure == "http":
            return httpx.Response(503, text="secret token")
        if failure == "malformed":
            return rpc_response({"list": [], "total": 2})
        if failure == "wrong_shop":
            return rpc_response({"list": [raw_order("order-2", shopId=999)], "total": 2})
        return rpc_response({"list": [raw_order()], "total": 2})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as error:
            await ElemeProvider(client).fetch_orders(connection, date(2026, 11, 17))
    assert "secret" not in str(error.value)
    assert "token" not in str(error.value)


async def test_page_limit_reports_incomplete(connection):
    connection.configuration["max_pages"] = 1

    def handler(request):
        if json.loads(request.content)["action"] == "eleme.order.queryRefundFoodInfos":
            return rpc_response({"goodsList": []})
        return rpc_response({"list": [raw_order()], "total": 2})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError, match="分页上限"):
            await ElemeProvider(client).fetch_orders(connection, date(2026, 11, 17))


@pytest.mark.parametrize("mode, expected", [("per_parent", "2"), ("order_total", "1")])
async def test_bundle_count_basis_is_explicit(connection, mode, expected):
    connection.configuration["bundle_quantity_mode"] = mode
    order = raw_order()
    order["groups"][0]["items"][0]["foodGroup"] = [
        {"name": "半只鸡", "skuId": "chicken", "quantity": 2}
    ]

    def handler(request):
        if json.loads(request.content)["action"] == "eleme.order.queryRefundFoodInfos":
            return rpc_response({"goodsList": []})
        return rpc_response({"list": [order], "total": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ElemeProvider(client).fetch_orders(connection, date(2026, 11, 17))
    assert result[0].items[0].components[0].quantity_per_unit == Decimal(expected)


async def test_unknown_bundle_semantics_fail_explicitly(connection):
    order = raw_order()
    order["groups"][0]["items"][0]["foodGroup"] = [{"name": "半只鸡", "quantity": 2}]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: rpc_response({"list": [order], "total": 1}))
    ) as client:
        with pytest.raises(ConfigurationError, match="bundle_quantity_mode"):
            await ElemeProvider(client).fetch_orders(connection, date(2026, 11, 17))


async def test_jd_mode_must_not_silently_downgrade_to_legacy(connection):
    connection.platform = "jd"
    connection.configuration["api_mode"] = "legacy"
    with pytest.raises(ConfigurationError, match="旧平台"):
        await JDProvider().fetch_orders(connection, date(2026, 11, 17))


def jd_order(order_id=123, **changes):
    return {
        "orderId": order_id,
        "deliveryStationNo": "123",
        "orderStartTime": "2026-11-17 00:30:00",
        "orderStatus": 90000,
        "orderNum": 7,
        "orderBuyerPayableMoney": 3980,
        "product": [
            {
                "supplyName": "product-uuid",
                "skuId": 900,
                "skuName": "半只鸡+半只鸭",
                "skuCount": 2,
                "skuJdPrice": 1990,
            }
        ],
        "buyerFullName": "不得保存",
        **changes,
    }


def jd_response(rows):
    return httpx.Response(
        200, json={"code": "0", "data": json.dumps({"code": "0", "result": {"resultList": rows}})}
    )


def fast_jd_provider(client):
    current = datetime(2026, 11, 17, tzinfo=UTC)

    async def sleep(seconds):
        nonlocal current
        current += timedelta(seconds=seconds)

    return JDProvider(client, clock=lambda: current, sleeper=sleep)


@pytest.mark.parametrize("failure", ["duplicate", "wrong_hour"])
async def test_jd_rejects_duplicate_pages_and_wrong_hour(connection, failure):
    connection.platform = "jd"
    connection.configuration["page_size"] = 2

    def handler(request):
        payload = json.loads(parse_qs(request.content.decode())["jd_param_json"][0])
        if failure == "wrong_hour":
            return jd_response([jd_order(orderStartTime="2026-11-17 02:30:00")])
        return jd_response([jd_order(), jd_order(124)] if payload["page"] == 1 else [jd_order(124)])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError):
            await fast_jd_provider(client).fetch_orders(connection, date(2026, 11, 17))


async def test_successful_partial_refund_does_not_clear_all_goods(connection):
    def handler(request):
        body = json.loads(request.content)
        if body["action"] == "eleme.order.getRefundOrder":
            return rpc_response(
                {
                    "orderId": "order-1",
                    "refundStatus": "successful",
                    "refundType": "part",
                    "totalPrice": "19.90",
                }
            )
        if body["action"] == "eleme.order.queryRefundFoodInfos":
            return rpc_response(
                {"goodsList": [{"uniqueId": "line-1", "refundedUnit": 1, "refundedMoney": "19.90"}]}
            )
        return rpc_response({"list": [raw_order(refundStatus="successful")], "total": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        orders = await ElemeProvider(client).fetch_orders(connection, date(2026, 11, 17))
    assert orders[0].refund_status == "partial"
    assert orders[0].items[0].refunded_quantity == Decimal("1")


async def test_jd_new_gateway_hour_windows_full_pagination_and_money(connection):
    connection.platform = "jd"
    requests = []
    signed_timestamps = []

    def handler(request):
        assert (
            str(request.url)
            == "https://openapi.jddj.com/djapi/ApiPlatformService/querySellerOrderPage"
        )
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        signed_timestamps.append(form["timestamp"])
        assert "method" not in form
        payload = json.loads(form["jd_param_json"])
        requests.append(payload)
        assert "listOrderRequest" not in payload
        assert "orgCode" not in payload
        assert payload["deliveryStationNo"] == "123"
        if payload["startTime"].endswith("00:00:00") and payload["page"] == 1:
            return jd_response([jd_order()])
        if payload["startTime"].endswith("00:00:00") and payload["page"] == 2:
            return jd_response([jd_order(124)])
        return jd_response([])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        orders = await fast_jd_provider(client).fetch_orders(connection, date(2026, 11, 17))
    assert len(orders) == 2
    assert len(requests) == 26  # 第一小时3页；其余23小时各1页。
    assert len(set(signed_timestamps)) == 26
    assert requests[0]["endTime"] == "2026-11-17 00:59:59"
    assert requests[-1]["endTime"] == "2026-11-17 23:59:59"
    assert orders[0].total_amount == Decimal("39.8")
    assert orders[0].items[0].unit_price == Decimal("19.9")
    assert orders[0].placed_at.tzinfo is UTC
    assert "不得保存" not in orders[0].model_dump_json()


@pytest.mark.parametrize(
    "failure", ["outer", "inner", "shape", "encrypted", "too_many", "wrong_shop"]
)
async def test_jd_failures_are_explicit_and_do_not_return_partial_day(connection, failure):
    connection.platform = "jd"

    def handler(request):
        payload = json.loads(parse_qs(request.content.decode())["jd_param_json"][0])
        if payload["startTime"].endswith("00:00:00"):
            return jd_response([jd_order()]) if payload["page"] == 1 else jd_response([])
        if failure == "outer":
            return httpx.Response(200, json={"code": "1", "msg": "secret"})
        if failure == "inner":
            return httpx.Response(200, json={"code": "0", "data": '{"code":"1"}'})
        if failure == "shape":
            return httpx.Response(
                200, json={"code": "0", "data": {"code": "0", "result": jd_order()}}
            )
        if failure == "encrypted":
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "encryptData": "ciphertext",
                    "data": {"code": "0", "result": {"resultList": []}},
                },
            )
        if failure == "too_many":
            return jd_response([jd_order(), jd_order(124)])
        return jd_response([jd_order(deliveryStationNo="999")])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as error:
            await fast_jd_provider(client).fetch_orders(connection, date(2026, 11, 17))
    assert "secret" not in str(error.value)


async def test_jd_page_limit_is_not_success(connection):
    connection.platform = "jd"
    connection.configuration["max_pages"] = 1
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: jd_response([jd_order()]))
    ) as client:
        with pytest.raises(ProviderError, match="分页上限"):
            await fast_jd_provider(client).fetch_orders(connection, date(2026, 11, 17))


async def test_eleme_java_millisecond_timestamp_and_full_refund(connection):
    order = raw_order(createdAt=1794899400000, refundStatus="successful")

    def handler(request):
        action = json.loads(request.content)["action"]
        if action == "eleme.order.getRefundOrder":
            return rpc_response(
                {
                    "orderId": "order-1",
                    "refundStatus": "successful",
                    "refundType": "normal",
                    "totalPrice": "39.80",
                }
            )
        return rpc_response({"list": [order], "total": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ElemeProvider(client).fetch_orders(connection, date(2026, 11, 17))
    assert result[0].refund_status == "full"
    assert result[0].items[0].refunded_quantity == Decimal("2")
    assert result[0].refund_amount == Decimal("39.80")


async def test_unknown_refund_status_cannot_be_recorded_as_no_refund(connection):
    order = raw_order(refundStatus="newUnknownRefundState")

    def handler(request):
        if json.loads(request.content)["action"] == "eleme.order.queryRefundFoodInfos":
            return rpc_response({"goodsList": []})
        return rpc_response({"list": [order], "total": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError, match="退单状态"):
            await ElemeProvider(client).fetch_orders(connection, date(2026, 11, 17))


async def test_full_refund_must_belong_to_the_requested_order(connection):
    def handler(request):
        if json.loads(request.content)["action"] == "eleme.order.getRefundOrder":
            return rpc_response(
                {
                    "orderId": "another-order",
                    "refundStatus": "successful",
                    "refundType": "normal",
                    "totalPrice": "39.80",
                }
            )
        return rpc_response({"list": [raw_order(refundStatus="successful")], "total": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError, match="退单订单号"):
            await ElemeProvider(client).fetch_orders(connection, date(2026, 11, 17))


async def test_negative_full_refund_is_invalid(connection):
    def handler(request):
        if json.loads(request.content)["action"] == "eleme.order.getRefundOrder":
            return rpc_response(
                {
                    "orderId": "order-1",
                    "refundStatus": "successful",
                    "refundType": "normal",
                    "totalPrice": "-1",
                }
            )
        return rpc_response({"list": [raw_order(refundStatus="successful")], "total": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError):
            await ElemeProvider(client).fetch_orders(connection, date(2026, 11, 17))


async def test_default_provider_reuses_and_closes_one_client_for_a_sync(connection, monkeypatch):
    original_client = httpx.AsyncClient
    clients = []

    def handler(request):
        if json.loads(request.content)["action"] == "eleme.order.queryRefundFoodInfos":
            return rpc_response({"goodsList": []})
        return rpc_response({"list": [raw_order()], "total": 1})

    def factory(*args, **kwargs):
        client = original_client(*args, transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    result = await ElemeProvider().fetch_orders(connection, date(2026, 11, 17))
    assert len(result) == 1
    assert len(clients) == 1
    assert clients[0].is_closed


def test_factory_rejects_unknown_platform():
    assert isinstance(get_provider("eleme"), ElemeProvider)
    assert isinstance(get_provider("jd"), JDProvider)
    with pytest.raises(ConfigurationError):
        get_provider("unknown")
