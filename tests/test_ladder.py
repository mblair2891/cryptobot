from __future__ import annotations

from decimal import Decimal

from aethergrid.domain.enums import GridMode
from aethergrid.domain.models import GridConfig, Product
from aethergrid.strategy.ladder import (
    arithmetic_prices,
    build_prices,
    fee_spread_cover_ok,
    geometric_prices,
    levels_from_step_pct,
    min_step_pct,
)


def test_geometric_inclusive_endpoints() -> None:
    prices = geometric_prices(Decimal("100"), Decimal("200"), 5)
    assert prices[0] == Decimal("100")
    assert prices[-1] == Decimal("200")
    assert len(prices) == 5
    ratios = [prices[i + 1] / prices[i] for i in range(len(prices) - 1)]
    for r in ratios[1:]:
        assert abs(r - ratios[0]) / ratios[0] < Decimal("0.0000001")


def test_arithmetic_equal_steps() -> None:
    prices = arithmetic_prices(Decimal("100"), Decimal("110"), 6)
    assert prices[0] == Decimal("100")
    assert prices[-1] == Decimal("110")
    steps = [prices[i + 1] - prices[i] for i in range(len(prices) - 1)]
    assert all(s == steps[0] for s in steps)


def test_levels_from_step_pct() -> None:
    n = levels_from_step_pct(Decimal("100"), Decimal("110"), Decimal("0.01"), GridMode.GEOMETRIC)
    assert n >= 8


def test_build_prices_snaps_to_tick(product: Product) -> None:
    cfg = GridConfig(
        product_id="BTC-USD",
        investment=Decimal("1000"),
        lower_price=Decimal("90000.001"),
        upper_price=Decimal("110000.009"),
        grid_levels=9,
    )
    prices = build_prices(cfg, product)
    for p in prices:
        assert (p / product.quote_increment) == (p / product.quote_increment).to_integral_value()
    assert all(prices[i] < prices[i + 1] for i in range(len(prices) - 1))


def test_fee_spread_cover() -> None:
    prices = geometric_prices(Decimal("100"), Decimal("110"), 6)
    assert fee_spread_cover_ok(prices, Decimal("0.001"), Decimal("0.0002"), Decimal("2"))
    tight = geometric_prices(Decimal("100"), Decimal("100.2"), 20)
    assert not fee_spread_cover_ok(tight, Decimal("0.006"), Decimal("0.001"), Decimal("2"))
    assert min_step_pct(prices) > 0
