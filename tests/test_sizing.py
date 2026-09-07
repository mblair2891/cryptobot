from __future__ import annotations

from decimal import Decimal

import pytest

from aethergrid.domain.models import GridConfig, Product
from aethergrid.strategy.sizing import SizingError, slot_base_size


def test_equal_quote_respects_min_notional(product: Product) -> None:
    cfg = GridConfig(
        product_id="BTC-USD",
        investment=Decimal("10000"),
        lower_price=Decimal("90000"),
        upper_price=Decimal("110000"),
        grid_levels=11,
    )
    size = slot_base_size(
        config=cfg,
        buy_price=Decimal("100000"),
        n_slots=10,
        product=product,
        fee_rate=Decimal("0.006"),
    )
    assert size > 0
    assert size * Decimal("100000") >= product.min_market_funds


def test_too_small_investment_raises(product: Product) -> None:
    cfg = GridConfig(
        product_id="BTC-USD",
        investment=Decimal("1"),
        lower_price=Decimal("90000"),
        upper_price=Decimal("110000"),
        grid_levels=50,
    )
    with pytest.raises(SizingError):
        slot_base_size(
            config=cfg,
            buy_price=Decimal("100000"),
            n_slots=49,
            product=product,
            fee_rate=Decimal("0.006"),
        )
