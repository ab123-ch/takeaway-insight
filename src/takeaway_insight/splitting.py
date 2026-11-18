"""结构化明细优先。名称推断可追溯，不能把推断伪装成平台明细。"""

from dataclasses import dataclass
from decimal import Decimal

from takeaway_insight.contracts import NormalizedLine


@dataclass(frozen=True)
class SplitPart:
    name: str
    quantity: Decimal
    refunded_quantity: Decimal
    unit: str
    source: str
    needs_review: bool = False
    rule_id: int | None = None


def split_line(line: NormalizedLine, rules: list[dict]) -> list[SplitPart]:
    """返回组件总量；数量已乘父行份数，调用方不得再次相乘。"""
    if line.components:
        return [
            SplitPart(
                c.name.strip(),
                line.quantity * c.quantity_per_unit,
                line.refunded_quantity * c.quantity_per_unit,
                c.unit,
                "platform",
            )
            for c in line.components
        ]
    for rule in rules:
        if rule.get("enabled", True) and rule["match_name"] == line.name:
            return [
                SplitPart(
                    c["name"].strip(),
                    line.quantity * Decimal(str(c["factor"])),
                    line.refunded_quantity * Decimal(str(c["factor"])),
                    c.get("unit", line.unit),
                    "rule",
                    rule_id=rule.get("id"),
                )
                for c in rule["components"]
            ]
    # 只在括号外识别 +，避免把“鸡腿饭（加辣+加饭）”拆成两个食品。
    chunks: list[str] = []
    current: list[str] = []
    depth = 0
    for char in line.name:
        if char in "（([{【":
            depth += 1
        elif char in "）)]}】":
            depth = max(0, depth - 1)
        if char in "+＋" and depth == 0:
            chunks.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    chunks.append("".join(current).strip())
    if len(chunks) > 1 and all(chunks):
        return [
            SplitPart(
                name,
                line.quantity,
                line.refunded_quantity,
                line.unit,
                "name_split",
                needs_review=True,
            )
            for name in chunks
        ]
    return [SplitPart(line.name, line.quantity, line.refunded_quantity, line.unit, "original")]
