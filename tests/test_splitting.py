from decimal import Decimal

from takeaway_insight.contracts import NormalizedComponent, NormalizedLine
from takeaway_insight.splitting import split_line


def test_text_combo_keeps_half_as_name_and_multiplies_parent_quantity():
    line = NormalizedLine(external_id="1", name="半只鸡+半只鸭", quantity="2")
    parts = split_line(line, [])
    assert [(p.name, p.quantity) for p in parts] == [
        ("半只鸡", Decimal("2")),
        ("半只鸭", Decimal("2")),
    ]
    assert all(p.source == "name_split" and p.needs_review for p in parts)


def test_platform_components_take_priority_over_name_and_rule():
    line = NormalizedLine(
        external_id="1",
        name="鸡+鸭",
        quantity=2,
        components=[NormalizedComponent(name="鸡翅", quantity_per_unit=3)],
    )
    parts = split_line(
        line, [{"id": 1, "match_name": "鸡+鸭", "components": [{"name": "鸡", "factor": "1"}]}]
    )
    assert [(p.name, p.quantity, p.source) for p in parts] == [("鸡翅", Decimal("6"), "platform")]


def test_explicit_rule_is_confirmed_and_refund_quantity_scales():
    line = NormalizedLine(external_id="1", name="双拼", quantity=3, refunded_quantity=1)
    parts = split_line(
        line,
        [
            {
                "id": 9,
                "match_name": "双拼",
                "components": [{"name": "鸡翅", "factor": "2"}, {"name": "可乐", "factor": "1"}],
            }
        ],
    )
    assert parts[0].quantity == 6
    assert parts[0].refunded_quantity == 2
    assert parts[0].rule_id == 9
    assert not parts[0].needs_review


def test_plus_inside_parentheses_does_not_break_one_product():
    parts = split_line(NormalizedLine(external_id="1", name="鸡腿饭（加辣+加饭）", quantity=1), [])
    assert len(parts) == 1
    assert parts[0].name == "鸡腿饭（加辣+加饭）"


def test_original_non_combo_keeps_fractional_quantity():
    parts = split_line(NormalizedLine(external_id="1", name="小炒肉", quantity="0.5"), [])
    assert parts[0].quantity == Decimal("0.5")
