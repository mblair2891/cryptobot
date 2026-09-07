from __future__ import annotations

from decimal import Decimal
from math import log

from aethergrid.domain.enums import GridMode, OrderSide
from aethergrid.domain.models import GridConfig, Product
from aethergrid.money import D, ONE, ZERO, buy_price, sell_price


def levels_from_step_pct(
    lower: Decimal,
    upper: Decimal,
    step_pct: Decimal,
    mode: GridMode,
) -> int:
    """Derive inclusive price-point count from a percent step."""
    if step_pct <= 0:
        raise ValueError("grid_step_pct must be positive")
    if lower <= 0 or upper <= lower:
        raise ValueError("invalid range")
    if mode == GridMode.GEOMETRIC:
        ratio = upper / lower
        n = int(round(log(float(ratio)) / log(float(ONE + step_pct)))) + 1
    else:
        step = lower * step_pct
        if step <= 0:
            raise ValueError("arithmetic step collapsed")
        n = int(round(float((upper - lower) / step))) + 1
    return max(2, n)


def geometric_prices(lower: Decimal, upper: Decimal, n: int) -> list[Decimal]:
    if n < 2:
        raise ValueError("need at least 2 price points")
    ratio = (upper / lower) ** (D(1) / D(n - 1))
    prices = [lower]
    current = lower
    for _ in range(n - 2):
        current *= ratio
        prices.append(current)
    prices.append(upper)
    return prices


def arithmetic_prices(lower: Decimal, upper: Decimal, n: int) -> list[Decimal]:
    if n < 2:
        raise ValueError("need at least 2 price points")
    step = (upper - lower) / D(n - 1)
    return [lower + step * D(i) for i in range(n)]


def snap_prices(raw: list[Decimal], product: Product) -> list[Decimal]:
    """Quantize ladder prices to the product tick without collapsing steps."""
    snapped: list[Decimal] = []
    tick = product.quote_increment
    for i, price in enumerate(raw):
        if i == 0:
            q = buy_price(price, tick)
        elif i == len(raw) - 1:
            q = sell_price(price, tick)
        else:
            q = buy_price(price, tick)
        if q <= ZERO:
            q = tick
        if snapped and q <= snapped[-1]:
            q = snapped[-1] + tick
        snapped.append(q)
    return snapped


def build_prices(config: GridConfig, product: Product) -> list[Decimal]:
    n = config.grid_levels
    if n is None:
        if config.grid_step_pct is None:
            raise ValueError("grid_levels or grid_step_pct required")
        n = levels_from_step_pct(
            config.lower_price, config.upper_price, config.grid_step_pct, config.mode
        )
    if config.mode == GridMode.GEOMETRIC:
        raw = geometric_prices(config.lower_price, config.upper_price, n)
    else:
        raw = arithmetic_prices(config.lower_price, config.upper_price, n)
    prices = snap_prices(raw, product)
    if len(prices) < 2:
        raise ValueError("ladder collapsed after tick snap")
    for a, b in zip(prices, prices[1:], strict=False):
        if b <= a:
            raise ValueError("non-increasing ladder after snap")
    return prices


def typical_step(prices: list[Decimal]) -> Decimal:
    if len(prices) < 2:
        return ZERO
    steps = [prices[i + 1] - prices[i] for i in range(len(prices) - 1)]
    steps.sort()
    return steps[len(steps) // 2]


def min_step_pct(prices: list[Decimal]) -> Decimal:
    best = Decimal("1")
    for i in range(len(prices) - 1):
        if prices[i] <= 0:
            continue
        pct = (prices[i + 1] - prices[i]) / prices[i]
        if pct < best:
            best = pct
    return best


def mark_gap_index(prices: list[Decimal], mark: Decimal) -> int:
    """Index i such that prices[i] <= mark < prices[i+1], clamped."""
    if mark <= prices[0]:
        return 0
    if mark >= prices[-1]:
        return len(prices) - 2
    for i in range(len(prices) - 1):
        if prices[i] <= mark < prices[i + 1]:
            return i
    return len(prices) - 2


def fee_spread_cover_ok(
    prices: list[Decimal],
    fee_rate: Decimal,
    spread_pct: Decimal,
    multiple: Decimal = Decimal("2"),
) -> bool:
    """True if typical grid step exceeds multiple * (fee + spread)."""
    step = min_step_pct(prices)
    hurdle = multiple * (fee_rate * 2 + spread_pct)
    return step > hurdle
