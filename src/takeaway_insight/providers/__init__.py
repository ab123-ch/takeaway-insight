"""官方订单连接器，缺少权限或未核实的协议必须显式失败。"""

from takeaway_insight.contracts import ConfigurationError, OrderProvider
from takeaway_insight.providers.eleme import ElemeProvider
from takeaway_insight.providers.jd import JDProvider


def get_provider(platform: str) -> OrderProvider:
    if platform == "eleme":
        return ElemeProvider()
    if platform == "jd":
        return JDProvider()
    raise ConfigurationError("不支持的订单平台")
