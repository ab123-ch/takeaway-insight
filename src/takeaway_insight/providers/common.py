"""只处理协议共用的配置、HTTP 与时间，不保存平台原始响应。"""

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from takeaway_insight.contracts import ConfigurationError, ConnectionConfig, ProviderError

SHANGHAI = ZoneInfo("Asia/Shanghai")


def credentials(connection: ConnectionConfig) -> tuple[str, str, str]:
    values = []
    for key in ("app_key_env", "app_secret_env", "access_token_env"):
        name = connection.configuration.get(key)
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError(f"门店 {connection.merchant_id} 缺少 {key} 环境变量名称配置")
        value = os.environ.get(name)
        if not value or not value.strip():
            raise ConfigurationError(f"门店 {connection.merchant_id} 的环境变量 {name} 未配置")
        values.append(value)
    return values[0], values[1], values[2]


def integer_option(connection: ConnectionConfig, name: str, default: int, maximum: int) -> int:
    value = connection.configuration.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ConfigurationError(f"{name} 必须是 1 到 {maximum} 的整数")
    return value


def timestamp(value: Any) -> datetime:
    """平台格式为中国时间；数值日期按 Java Date 的毫秒编码处理。"""
    if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=int(value * 1000))
    if not isinstance(value, str) or not value:
        raise ValueError("缺少订单时间")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(UTC)


def json_value(value: Any) -> Any:
    return json.loads(value, parse_float=Decimal) if isinstance(value, str) else value


class HTTPProvider:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client

    async def post(self, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            if self.client is None:
                async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                    response = await client.post(url, **kwargs)
            else:
                response = await self.client.post(url, timeout=20, **kwargs)
            response.raise_for_status()
            result = json.loads(response.content, parse_float=Decimal)
            if not isinstance(result, dict):
                raise ValueError("响应不是对象")
            return result
        except (httpx.HTTPError, ValueError, TypeError):
            # 不输出请求 URL、响应正文或平台错误消息，避免密钥/顾客信息进入日志。
            raise ProviderError("平台请求失败或返回无效 JSON，本次同步未完成") from None
