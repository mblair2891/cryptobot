from __future__ import annotations

from decimal import Decimal

from aethergrid.domain.enums import SizeMode
from aethergrid.domain.models import GridConfig, Product
from aethergrid.money import ZERO, base_size, fee_from_notional, notional


class SizingError(ValueError):
    pass


def slot_count(n_prices: int) -> int:
    if n_prices < 2:
        raise SizingError("need at least 2 prices")
    return n_prices - 1


def quote_per_slot(investment: Decimal, n_slots: int) -> Decimal:
    if n_slots <= 0:
        raise SizingError("no slots")
    q = investment / Decimal(n_slots)
    if q <= 0:
        raise SizingError("investment too small for grid")
    return q


def slot_base_size(
    *,
    config: GridConfig,
    buy_price: Decimal,
    n_slots: int,
    product: Product,
    fee_rate: Decimal,
) -> Decimal:
    if config.size_mode == SizeMode.EQUAL_QUOTE:
        quote = quote_per_slot(config.investment, n_slots)
        # Size so quote + fee still respects min notional.
        raw = quote / buy_price
    else:
        mid = (config.lower_price + config.upper_price) / Decimal("2")
        raw = quote_per_slot(config.investment, n_slots) / mid

    sized = product.q_size(raw)
    if sized <= 0:
        raise SizingError(f"base size quantized to 0 at {buy_price}")

    notion = notional(buy_price, sized)
    fee = fee_from_notional(notion, fee_rate)
    min_funds = max(product.min_market_funds, product.quote_min_size, product.base_min_size * buy_price)
    if notion + fee < min_funds:
        # Try bumping to min notional after fees — but never inflate the whole grid past allocation.
        need = min_funds / (Decimal("1") - fee_rate) if fee_rate < 1 else min_funds
        bumped = product.q_size(need / buy_price)
        bumped_notion = notional(buy_price, bumped) if bumped > 0 else ZERO
        if (
            bumped <= 0
            or bumped_notion + fee_from_notional(bumped_notion, fee_rate) < min_funds
            or bumped_notion * Decimal(n_slots) > config.investment * Decimal("1.05")
        ):
            raise SizingError(
                f"order below min notional after fees at {buy_price}: {notion} < {min_funds}"
            )
        sized = bumped
        notion = bumped_notion

    if product.base_min_size > 0 and sized < product.base_min_size:
        raise SizingError(f"size {sized} < base_min_size {product.base_min_size}")
    if product.base_max_size is not None and sized > product.base_max_size:
        raise SizingError(f"size {sized} > base_max_size")
    return sized


def assert_allocation_fits(
    *,
    investment: Decimal,
    n_slots: int,
    buy_prices: list[Decimal],
    sizes: list[Decimal],
    cash_available: Decimal,
    min_cash_reserve: Decimal,
) -> None:
    reserved = sum((p * s for p, s in zip(buy_prices, sizes, strict=False)), ZERO)
    if reserved > investment * Decimal("1.05"):
        # Geometric equal-quote should be ~investment; equal-base can differ.
        pass
    if cash_available - min_cash_reserve < min(sizes[0] * buy_prices[0], investment):
        raise SizingError("insufficient cash after reserve for even one slot")
    _ = n_slots
