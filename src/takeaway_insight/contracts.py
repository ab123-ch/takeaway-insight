"""平台与应用服务共用的数据合同，不包含顾客隐私或平台密钥。"""

from datetime import date
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, Field, model_validator

Platform = Literal["eleme", "jd"]
OrderStatus = Literal["pending", "accepted", "completed", "cancelled"]
RefundStatus = Literal["none", "partial", "full", "unknown"]


class NormalizedComponent(BaseModel):
    """一份父商品中包含的子商品数量；组件数须乘以父商品购买数量。"""

    name: str = Field(min_length=1)
    quantity_per_unit: Decimal = Field(default=Decimal("1"), gt=0, max_digits=16, decimal_places=4)
    sku_id: str = ""
    unit: str = "份"


class NormalizedLine(BaseModel):
    """平台原始商品行。refunded_quantity 仅表示已确认退款的商品数量。"""

    external_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0, max_digits=16, decimal_places=4)
    sku_id: str = ""
    unit: str = "份"
    unit_price: Decimal | None = Field(default=None, ge=0)
    refunded_quantity: Decimal = Field(default=Decimal("0"), ge=0)
    components: list[NormalizedComponent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_refund(self) -> "NormalizedLine":
        if self.refunded_quantity > self.quantity:
            raise ValueError("退款数量不能大于商品购买数量")
        return self


class NormalizedOrder(BaseModel):
    """平台订单的经营分析字段；金额统一为元、时间必须含时区。"""

    external_id: str = Field(min_length=1)
    placed_at: AwareDatetime
    status: OrderStatus = "pending"
    serial_no: str = ""
    completed_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    total_amount: Decimal = Field(default=Decimal("0"), ge=0)
    refund_status: RefundStatus = "none"
    refund_amount: Decimal = Field(default=Decimal("0"), ge=0)
    items: list[NormalizedLine] = Field(default_factory=list)


class ConnectionConfig(BaseModel):
    """连接器配置仅保存环境变量名称，由对应门店精确引用实际密钥。"""

    merchant_id: int
    platform: Platform
    external_shop_id: str = Field(min_length=1)
    configuration: dict[str, Any] = Field(default_factory=dict)


class ProviderError(Exception):
    """可安全向用户显示、不包含请求签名和凭证的同步错误。"""


class ConfigurationError(ProviderError):
    """该门店当前缺少必要平台配置。"""


class OrderProvider(Protocol):
    """完整拉取指定营业日，任意分页失败必须抛出异常而非返回部分数据。"""

    async def fetch_orders(
        self, connection: ConnectionConfig, target_date: date
    ) -> list[NormalizedOrder]: ...
