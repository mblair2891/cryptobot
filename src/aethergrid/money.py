from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
ONE = Decimal("1")
BPS = Decimal("10000")


def D(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return ZERO
    return Decimal(str(value))


def quantize_down(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        return value
    n = (value / increment).to_integral_value(rounding=ROUND_DOWN)
    return n * increment


def quantize_up(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        return value
    n = (value / increment).to_integral_value(rounding=ROUND_CEILING)
    return n * increment


def quantize_nearest(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        return value
    n = (value / increment).to_integral_value(rounding=ROUND_HALF_UP)
    return n * increment


def buy_price(value: Decimal, tick: Decimal) -> Decimal:
    """Buy limits never round up through the intended price."""
    return quantize_down(value, tick)


def sell_price(value: Decimal, tick: Decimal) -> Decimal:
    """Sell limits never round down through the intended price."""
    return quantize_up(value, tick)


def base_size(value: Decimal, increment: Decimal) -> Decimal:
    sized = quantize_down(value, increment)
    return sized if sized > 0 else ZERO


def notional(price: Decimal, size: Decimal) -> Decimal:
    return price * size


def fee_from_notional(notional_value: Decimal, fee_rate: Decimal) -> Decimal:
    return (notional_value * fee_rate).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def pct(value: Decimal, basis: Decimal) -> Decimal:
    if basis == 0:
        return ZERO
    return value / basis


def clamp(value: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    return max(lo, min(hi, value))


def almost_eq(a: Decimal, b: Decimal, eps: Decimal = Decimal("1e-12")) -> bool:
    return abs(a - b) <= eps


def is_positive(value: Decimal) -> bool:
    return value > 0


def floor_to(value: Decimal, places: int) -> Decimal:
    q = Decimal("1").scaleb(-places)
    return value.quantize(q, rounding=ROUND_FLOOR)
