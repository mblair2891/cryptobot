from __future__ import annotations

from decimal import Decimal

from aethergrid.domain.enums import GridMode, SlotState
from aethergrid.domain.models import BotRuntime, GridConfig, GridSlot, Product
from aethergrid.money import ZERO
from aethergrid.strategy.ladder import build_prices
from aethergrid.strategy.sizing import slot_base_size


def should_trail_up(config: GridConfig, mark: Decimal, atr: Decimal | None = None) -> bool:
    if not config.trailing_up:
        return False
    trigger = config.upper_price * (Decimal("1") + config.trailing_up_trigger_pct)
    if atr and atr > 0:
        trigger = min(trigger, config.upper_price + atr)
    return mark > trigger


def should_trail_down(config: GridConfig, mark: Decimal, atr: Decimal | None = None) -> bool:
    if not config.trailing_down:
        return False
    trigger = config.lower_price * (Decimal("1") - config.trailing_down_trigger_pct)
    if atr and atr > 0:
        trigger = max(trigger, config.lower_price - atr)
    return mark < trigger


def shift_range_up(config: GridConfig, mark: Decimal) -> tuple[Decimal, Decimal]:
    width_ratio = config.upper_price / config.lower_price
    width_abs = config.upper_price - config.lower_price
    new_upper = max(mark, config.upper_price)
    if config.mode == GridMode.GEOMETRIC:
        new_lower = new_upper / width_ratio
    else:
        new_lower = new_upper - width_abs
    if new_lower <= 0:
        new_lower = config.lower_price
    return new_lower, new_upper


def shift_range_down(config: GridConfig, mark: Decimal) -> tuple[Decimal, Decimal]:
    width_ratio = config.upper_price / config.lower_price
    width_abs = config.upper_price - config.lower_price
    new_lower = min(mark, config.lower_price)
    if new_lower <= 0:
        new_lower = config.lower_price
        return config.lower_price, config.upper_price
    if config.mode == GridMode.GEOMETRIC:
        new_upper = new_lower * width_ratio
    else:
        new_upper = new_lower + width_abs
    return new_lower, new_upper


def remap_holdings(
    old_slots: list[GridSlot],
    new_prices: list[Decimal],
    product: Product,
    config: GridConfig,
    fee_rate: Decimal,
    mark: Decimal,
) -> list[GridSlot]:
    """Rebuild slots after a range shift, keeping held base honest."""
    n_slots = len(new_prices) - 1
    new_slots: list[GridSlot] = []
    for i in range(n_slots):
        buy_p = new_prices[i]
        sell_p = new_prices[i + 1]
        target = slot_base_size(
            config=config,
            buy_price=buy_p,
            n_slots=n_slots,
            product=product,
            fee_rate=fee_rate,
        )
        new_slots.append(
            GridSlot(
                index=i,
                buy_price=buy_p,
                sell_price=sell_p,
                target_base=target,
                state=SlotState.EMPTY,
            )
        )

    lots: list[tuple[Decimal, Decimal]] = []  # (held_base, origin_buy_price)
    for slot in old_slots:
        if slot.held_base > 0:
            lots.append((slot.held_base, slot.buy_price))

    for held, origin in lots:
        remaining = held
        # Place holding into the slot whose sell is just above origin, else nearest above mark.
        target_idx = _best_holding_slot(new_slots, origin, mark)
        if target_idx is None:
            # Inventory exists but no sell slot — park on highest slot still above a floor.
            target_idx = len(new_slots) - 1
        slot = new_slots[target_idx]
        slot.held_base += remaining
        if slot.held_base > 0:
            if slot.sell_price > mark:
                slot.state = SlotState.SELL_OPEN
                slot.sell_remaining = min(slot.held_base, slot.target_base)
                if slot.held_base > slot.target_base:
                    # Overflow extra base into higher slots.
                    extra = slot.held_base - slot.target_base
                    slot.held_base = slot.target_base
                    slot.sell_remaining = slot.target_base
                    _spill_up(new_slots, target_idx + 1, extra, mark)
            else:
                slot.state = SlotState.HOLDING

    for slot in new_slots:
        if slot.held_base <= 0 and slot.buy_price < mark:
            slot.state = SlotState.BUY_OPEN
            slot.buy_remaining = slot.target_base
        elif slot.held_base <= 0:
            slot.state = SlotState.EMPTY
    return new_slots


def _best_holding_slot(slots: list[GridSlot], origin_buy: Decimal, mark: Decimal) -> int | None:
    # Prefer slot whose buy_price is closest to origin and sell is above mark.
    best_i: int | None = None
    best_dist: Decimal | None = None
    for slot in slots:
        if slot.sell_price <= mark:
            continue
        dist = abs(slot.buy_price - origin_buy)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_i = slot.index
    if best_i is not None:
        return best_i
    for slot in reversed(slots):
        if slot.sell_price > origin_buy:
            return slot.index
    return None


def _spill_up(slots: list[GridSlot], start: int, extra: Decimal, mark: Decimal) -> None:
    remaining = extra
    for i in range(start, len(slots)):
        if remaining <= ZERO:
            return
        slot = slots[i]
        room = slot.target_base - slot.held_base
        if room <= ZERO:
            continue
        take = min(room, remaining)
        slot.held_base += take
        remaining -= take
        if slot.sell_price > mark:
            slot.state = SlotState.SELL_OPEN
            slot.sell_remaining = slot.held_base
        else:
            slot.state = SlotState.HOLDING
    # If still leftover, dump onto last slot (honest inventory, oversized hold).
    if remaining > ZERO and slots:
        slots[-1].held_base += remaining
        if slots[-1].sell_price > mark:
            slots[-1].state = SlotState.SELL_OPEN
            slots[-1].sell_remaining = slots[-1].held_base
        else:
            slots[-1].state = SlotState.HOLDING


def apply_range_shift(
    runtime: BotRuntime,
    product: Product,
    fee_rate: Decimal,
    mark: Decimal,
    new_lower: Decimal,
    new_upper: Decimal,
) -> BotRuntime:
    runtime.config.lower_price = new_lower
    runtime.config.upper_price = new_upper
    runtime.prices = build_prices(runtime.config, product)
    runtime.slots = remap_holdings(
        runtime.slots, runtime.prices, product, runtime.config, fee_rate, mark
    )
    runtime.last_mark = mark
    # Existing exchange orders are stale after a shift; caller cancels all and replaces.
    runtime.open_orders = {}
    for slot in runtime.slots:
        slot.buy_order_id = None
        slot.sell_order_id = None
    return runtime
