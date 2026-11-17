from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from takeaway_insight.contracts import NormalizedComponent, NormalizedLine, NormalizedOrder


def test_quantity_preserves_fraction_and_components_are_per_parent_unit():
    line = NormalizedLine(
        external_id="line-1",
        name="半只鸡+半只鸭",
        quantity="2.5",
        components=[NormalizedComponent(name="半只鸡", quantity_per_unit="1")],
    )
    assert line.quantity == Decimal("2.5")
    assert line.components[0].quantity_per_unit * line.quantity == Decimal("2.5")


def test_order_requires_timezone_and_nonempty_identity():
    with pytest.raises(ValidationError):
        NormalizedOrder(external_id="", placed_at=datetime(2026, 11, 17, 1), items=[])


def test_refunded_quantity_must_not_exceed_original():
    with pytest.raises(ValidationError):
        NormalizedLine(external_id="x", name="鸡", quantity=1, refunded_quantity=2)
