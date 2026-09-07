from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from aethergrid.domain.enums import (
    BotStatus,
    IntentKind,
    OrderSide,
    OrderStatus,
    SlotState,
    StartMode,
    TimeInForce,
)
from aethergrid.domain.ids import client_order_id, flatten_order_id
from aethergrid.domain.models import (
    BotRuntime,
    Fill,
    GridConfig,
    GridSlot,
    Inventory,
    Order,
    OrderIntent,
    Product,
    TrailingState,
)
from aethergrid.money import ZERO, fee_from_notional
from aethergrid.strategy.ladder import build_prices, mark_gap_index
from aethergrid.strategy.sizing import slot_base_size
from aethergrid.strategy.trailing import (
    apply_range_shift,
    shift_range_down,
    shift_range_up,
    should_trail_down,
    should_trail_up,
)


class GridError(RuntimeError):
    pass


class GridEngine:
    """Bitsgap-style range grid. Pure state machine: events in, intents out."""

    def __init__(self, product: Product, fee_rate: Decimal) -> None:
        self.product = product
        self.fee_rate = fee_rate

    def initialize(
        self,
        config: GridConfig,
        mark: Decimal,
        quote_cash: Decimal,
        *,
        venue_base: Decimal = ZERO,
        bot_id: str | None = None,
    ) -> BotRuntime:
        prices = build_prices(config, self.product)
        n_slots = len(prices) - 1
        slots: list[GridSlot] = []
        for i in range(n_slots):
            buy_p = prices[i]
            sell_p = prices[i + 1]
            target = slot_base_size(
                config=config,
                buy_price=buy_p,
                n_slots=n_slots,
                product=self.product,
                fee_rate=self.fee_rate,
            )
            slots.append(
                GridSlot(
                    index=i,
                    buy_price=buy_p,
                    sell_price=sell_p,
                    target_base=target,
                )
            )
        starting_base = ZERO
        starting_quote = min(config.investment, quote_cash)
        if config.start_mode == StartMode.SPLIT:
            # Convert ~half the allocation into base so sells above mark can rest.
            half = starting_quote / Decimal("2")
            starting_base = self.product.q_size(half / mark) if mark > 0 else ZERO
            starting_quote = starting_quote - starting_base * mark
            self._seed_holdings(slots, starting_base, mark)

        self._arm_slots(slots, mark, entries_paused=False)

        runtime = BotRuntime(
            config=config,
            status=BotStatus.RUNNING,
            prices=prices,
            slots=slots,
            inventory=Inventory(
                base=starting_base,
                quote=starting_quote,
                starting_base=starting_base,
                starting_quote=min(config.investment, quote_cash),
            ),
            trailing=TrailingState(
                enabled_up=config.trailing_up,
                enabled_down=config.trailing_down,
                original_lower=config.lower_price,
                original_upper=config.upper_price,
            ),
            last_mark=mark,
            peak_equity=min(config.investment, quote_cash),
        )
        if bot_id:
            runtime.bot_id = bot_id
        return runtime

    def desired_intents(self, runtime: BotRuntime, mark: Decimal) -> list[OrderIntent]:
        """Diff slot desired orders vs working orders and emit place/cancel."""
        if runtime.status not in {
            BotStatus.RUNNING,
            BotStatus.PAUSED,
            BotStatus.COOLDOWN,
            BotStatus.STARTING,
        }:
            return []
        entries_paused = (
            runtime.status == BotStatus.PAUSED
            or runtime.protection.entries_paused
            or runtime.status == BotStatus.COOLDOWN
        )
        self._arm_slots(runtime.slots, mark, entries_paused=entries_paused)
        self._enforce_inventory_caps(runtime, mark)

        desired: dict[str, tuple[GridSlot, OrderSide, Decimal, Decimal]] = {}
        for slot in runtime.slots:
            if slot.held_base > 0 and slot.sell_remaining > 0 and self._can_sell(runtime, slot):
                desired[f"{slot.index}:SELL"] = (
                    slot,
                    OrderSide.SELL,
                    slot.sell_price,
                    slot.sell_remaining,
                )
            if (
                slot.buy_remaining > 0
                and slot.state in {SlotState.BUY_OPEN, SlotState.SELL_OPEN}
                and self._can_buy(runtime, slot, mark)
            ):
                desired[f"{slot.index}:BUY"] = (
                    slot,
                    OrderSide.BUY,
                    slot.buy_price,
                    slot.buy_remaining,
                )

        intents: list[OrderIntent] = []
        working_by_slot: dict[tuple[int, OrderSide], Order] = {}
        for order in runtime.open_orders.values():
            if not order.is_open or order.slot_index is None:
                continue
            working_by_slot[(order.slot_index, order.side)] = order

        # Cancel working orders that are no longer desired or have the wrong price/size.
        for (idx, side), order in list(working_by_slot.items()):
            key = f"{idx}:{side.value}"
            if key not in desired:
                intents.append(
                    OrderIntent(
                        kind=IntentKind.CANCEL,
                        cancel_client_order_id=order.client_order_id,
                        slot_index=idx,
                        reason="no longer desired",
                    )
                )
                continue
            slot, d_side, price, size = desired[key]
            if order.price != price or order.remaining != size:
                intents.append(
                    OrderIntent(
                        kind=IntentKind.REPLACE,
                        cancel_client_order_id=order.client_order_id,
                        client_order_id=self._next_oid(runtime, slot.index, d_side),
                        side=d_side,
                        price=price,
                        size=size,
                        slot_index=slot.index,
                        reason="price/size changed",
                    )
                )

        for key, (slot, side, price, size) in desired.items():
            existing = working_by_slot.get((slot.index, side))
            if existing and existing.is_open and existing.price == price and existing.remaining == size:
                continue
            if existing and existing.is_open:
                continue  # replace already queued
            intents.append(
                OrderIntent(
                    kind=IntentKind.PLACE,
                    client_order_id=self._next_oid(runtime, slot.index, side),
                    side=side,
                    price=price,
                    size=size,
                    slot_index=slot.index,
                    reason=f"slot {slot.index} {side}",
                )
            )
        return intents

    def on_fill(self, runtime: BotRuntime, fill: Fill) -> BotRuntime:
        slot = self._slot_for_fill(runtime, fill)
        fee = fill.fee if fill.fee > 0 else fee_from_notional(fill.price * fill.size, self.fee_rate)
        fill.fee = fee
        runtime.inventory.fees_paid += fee

        if fill.side == OrderSide.BUY:
            cost = fill.price * fill.size + fee
            runtime.inventory.base += fill.size
            runtime.inventory.quote -= cost
            if slot:
                slot.held_base += fill.size
                slot.buy_remaining = max(ZERO, slot.buy_remaining - fill.size)
                slot.sell_remaining = slot.held_base
                if slot.buy_remaining <= ZERO:
                    slot.buy_remaining = ZERO
                    slot.buy_order_id = None
                    slot.state = SlotState.SELL_OPEN
                else:
                    # Partial buy: keep rest of buy working and list paired sell.
                    slot.state = SlotState.BUY_OPEN
        else:
            proceeds = fill.price * fill.size - fee
            runtime.inventory.base -= fill.size
            if runtime.inventory.base < 0:
                runtime.inventory.base = ZERO
            runtime.inventory.quote += proceeds
            if slot:
                matched_cost = slot.buy_price * fill.size
                runtime.inventory.realized_pnl += proceeds - matched_cost
                slot.held_base = max(ZERO, slot.held_base - fill.size)
                slot.sell_remaining = max(ZERO, slot.sell_remaining - fill.size)
                if slot.held_base <= ZERO:
                    slot.held_base = ZERO
                    slot.sell_remaining = ZERO
                    slot.sell_order_id = None
                    slot.state = SlotState.BUY_OPEN
                    slot.buy_remaining = slot.target_base
                    runtime.cycles_completed += 1
                else:
                    slot.state = SlotState.SELL_OPEN

        order = runtime.open_orders.get(fill.client_order_id)
        if order:
            order.filled_size += fill.size
            order.filled_value += fill.price * fill.size
            order.fee += fee
            order.updated_at = datetime.now(UTC)
            if order.remaining <= ZERO:
                order.status = OrderStatus.FILLED
                runtime.open_orders.pop(fill.client_order_id, None)
                if slot:
                    if fill.side == OrderSide.BUY:
                        slot.buy_order_id = None
                    else:
                        slot.sell_order_id = None
            else:
                order.status = OrderStatus.PARTIAL

        runtime.updated_at = datetime.now(UTC)
        self._touch_daily(runtime, fill)
        return runtime

    def on_order_ack(self, runtime: BotRuntime, order: Order) -> BotRuntime:
        runtime.open_orders[order.client_order_id] = order
        if order.slot_index is not None and 0 <= order.slot_index < len(runtime.slots):
            slot = runtime.slots[order.slot_index]
            if order.side == OrderSide.BUY:
                slot.buy_order_id = order.client_order_id
            else:
                slot.sell_order_id = order.client_order_id
        runtime.updated_at = datetime.now(UTC)
        return runtime

    def on_cancel_ack(self, runtime: BotRuntime, client_order_id_value: str) -> BotRuntime:
        order = runtime.open_orders.pop(client_order_id_value, None)
        if order and order.slot_index is not None and 0 <= order.slot_index < len(runtime.slots):
            slot = runtime.slots[order.slot_index]
            if slot.buy_order_id == client_order_id_value:
                slot.buy_order_id = None
            if slot.sell_order_id == client_order_id_value:
                slot.sell_order_id = None
        return runtime

    def check_exits(self, runtime: BotRuntime, mark: Decimal) -> OrderIntent | None:
        cfg = runtime.config
        equity = runtime.inventory.equity(mark)
        if equity > runtime.peak_equity:
            runtime.peak_equity = equity
        start = runtime.inventory.starting_equity
        if start <= 0:
            return None
        if cfg.take_profit_pct is not None:
            if runtime.inventory.realized_pnl >= cfg.take_profit_pct * cfg.investment:
                return OrderIntent(kind=IntentKind.STOP, reason="take_profit")
        if cfg.stop_loss_pct is not None:
            pnl = equity - start
            if pnl <= -cfg.stop_loss_pct * start:
                return OrderIntent(
                    kind=IntentKind.FLATTEN if cfg.flatten_on_stop else IntentKind.STOP,
                    reason="stop_loss",
                )
        return None

    def maybe_trail(self, runtime: BotRuntime, mark: Decimal, atr_value: Decimal | None = None) -> bool:
        cfg = runtime.config
        shifted = False
        if should_trail_up(cfg, mark, atr_value):
            lower, upper = shift_range_up(cfg, mark)
            apply_range_shift(runtime, self.product, self.fee_rate, mark, lower, upper)
            runtime.trailing.shifts_up += 1
            runtime.trailing.last_shift_at = datetime.now(UTC)
            shifted = True
        elif should_trail_down(cfg, mark, atr_value):
            lower, upper = shift_range_down(cfg, mark)
            apply_range_shift(runtime, self.product, self.fee_rate, mark, lower, upper)
            runtime.trailing.shifts_down += 1
            runtime.trailing.last_shift_at = datetime.now(UTC)
            shifted = True
        runtime.last_mark = mark
        return shifted

    def add_funds(self, runtime: BotRuntime, quote_amount: Decimal, mark: Decimal) -> BotRuntime:
        if quote_amount <= 0:
            raise GridError("add_funds requires positive quote")
        runtime.config.investment += quote_amount
        runtime.inventory.quote += quote_amount
        runtime.inventory.starting_quote += quote_amount
        n_slots = len(runtime.slots)
        if n_slots == 0:
            return runtime
        for slot in runtime.slots:
            slot.target_base = slot_base_size(
                config=runtime.config,
                buy_price=slot.buy_price,
                n_slots=n_slots,
                product=self.product,
                fee_rate=self.fee_rate,
            )
            if slot.state in {SlotState.BUY_OPEN, SlotState.EMPTY} and slot.held_base <= 0:
                if slot.buy_price < (mark or slot.buy_price + 1):
                    slot.state = SlotState.BUY_OPEN
                    slot.buy_remaining = slot.target_base
        return runtime

    def reconfigure_range(
        self,
        runtime: BotRuntime,
        mark: Decimal,
        *,
        lower: Decimal | None = None,
        upper: Decimal | None = None,
        levels: int | None = None,
    ) -> BotRuntime:
        if lower is not None:
            runtime.config.lower_price = lower
        if upper is not None:
            runtime.config.upper_price = upper
        if levels is not None:
            runtime.config.grid_levels = levels
        apply_range_shift(
            runtime,
            self.product,
            self.fee_rate,
            mark,
            runtime.config.lower_price,
            runtime.config.upper_price,
        )
        return runtime

    def cancel_all_intents(self, runtime: BotRuntime, reason: str) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        for order in list(runtime.open_orders.values()):
            if order.is_open:
                intents.append(
                    OrderIntent(
                        kind=IntentKind.CANCEL,
                        cancel_client_order_id=order.client_order_id,
                        slot_index=order.slot_index,
                        reason=reason,
                    )
                )
        return intents

    def flatten_intents(self, runtime: BotRuntime, mark: Decimal) -> list[OrderIntent]:
        intents = self.cancel_all_intents(runtime, "flatten")
        base = runtime.inventory.base
        if base > 0 and mark > 0:
            size = self.product.q_size(base)
            if size > 0:
                runtime.order_seq += 1
                intents.append(
                    OrderIntent(
                        kind=IntentKind.FLATTEN,
                        client_order_id=flatten_order_id(runtime.bot_id, runtime.order_seq),
                        side=OrderSide.SELL,
                        price=mark,
                        size=size,
                        tif=TimeInForce.IOC,
                        reason="flatten inventory",
                    )
                )
        return intents

    def pause_entries(self, runtime: BotRuntime) -> BotRuntime:
        runtime.status = BotStatus.PAUSED
        runtime.protection.entries_paused = True
        runtime.protection.last_reason = "pause_entries"
        return runtime

    def resume(self, runtime: BotRuntime) -> BotRuntime:
        runtime.status = BotStatus.RUNNING
        runtime.protection.entries_paused = False
        runtime.protection.last_reason = ""
        runtime.cooldown_until = None
        return runtime

    # ------------------------------------------------------------------ internals

    def _next_oid(self, runtime: BotRuntime, slot_index: int, side: OrderSide) -> str:
        runtime.order_seq += 1
        if 0 <= slot_index < len(runtime.slots):
            runtime.slots[slot_index].seq = runtime.order_seq
        return client_order_id(runtime.bot_id, slot_index, side, runtime.order_seq)

    def _arm_slots(self, slots: list[GridSlot], mark: Decimal, entries_paused: bool) -> None:
        for slot in slots:
            if slot.held_base > 0:
                slot.sell_remaining = slot.held_base
                if slot.buy_remaining > 0 and not entries_paused and slot.buy_price < mark:
                    slot.state = SlotState.BUY_OPEN
                else:
                    slot.state = SlotState.SELL_OPEN
                    if entries_paused:
                        slot.buy_remaining = ZERO
                continue
            if entries_paused:
                slot.state = SlotState.EMPTY
                slot.buy_remaining = ZERO
                continue
            if slot.buy_price < mark:
                slot.state = SlotState.BUY_OPEN
                if slot.buy_remaining <= 0:
                    slot.buy_remaining = slot.target_base
            else:
                slot.state = SlotState.EMPTY
                slot.buy_remaining = ZERO
                slot.sell_remaining = ZERO

    def _seed_holdings(self, slots: list[GridSlot], base: Decimal, mark: Decimal) -> None:
        remaining = base
        # Fill sell-side slots from the mark upward.
        for slot in slots:
            if remaining <= ZERO:
                return
            if slot.sell_price <= mark:
                continue
            take = min(slot.target_base, remaining)
            slot.held_base = take
            slot.sell_remaining = take
            slot.state = SlotState.SELL_OPEN
            remaining -= take
        if remaining > ZERO and slots:
            slots[-1].held_base += remaining
            slots[-1].sell_remaining = slots[-1].held_base
            slots[-1].state = SlotState.SELL_OPEN

    def _can_buy(self, runtime: BotRuntime, slot: GridSlot, mark: Decimal) -> bool:
        if slot.buy_price >= mark:
            return False
        cap = runtime.config.inventory_cap
        if cap is None:
            cap = runtime.planned_base()
        if cap > 0 and runtime.inventory.base + slot.buy_remaining > cap:
            return False
        need = slot.buy_remaining * slot.buy_price
        need += fee_from_notional(need, self.fee_rate)
        available = runtime.inventory.quote - runtime.open_buy_quote()
        if available < need:
            return False
        # Do not over-buy past remaining allocation vs starting equity.
        invested = runtime.inventory.starting_quote - runtime.inventory.quote + runtime.open_buy_quote()
        if invested + need > runtime.config.investment * Decimal("1.02"):
            return False
        return True

    def _can_sell(self, runtime: BotRuntime, slot: GridSlot) -> bool:
        if slot.held_base <= ZERO:
            return False
        # Never sell what we do not hold, including reserved sells.
        reserved_other = ZERO
        for order in runtime.open_orders.values():
            if order.is_open and order.side == OrderSide.SELL and order.slot_index != slot.index:
                reserved_other += order.remaining
        return runtime.inventory.base - reserved_other >= slot.sell_remaining

    def _enforce_inventory_caps(self, runtime: BotRuntime, mark: Decimal) -> None:
        _ = mark
        cap = runtime.config.inventory_cap
        if cap is None:
            return
        if runtime.inventory.base >= cap:
            for slot in runtime.slots:
                if slot.held_base <= 0:
                    slot.state = SlotState.EMPTY
                    slot.buy_remaining = ZERO

    def _slot_for_fill(self, runtime: BotRuntime, fill: Fill) -> GridSlot | None:
        if fill.slot_index is not None and 0 <= fill.slot_index < len(runtime.slots):
            return runtime.slots[fill.slot_index]
        order = runtime.open_orders.get(fill.client_order_id)
        if order and order.slot_index is not None and 0 <= order.slot_index < len(runtime.slots):
            fill.slot_index = order.slot_index
            return runtime.slots[order.slot_index]
        # Fallback: match by price.
        for slot in runtime.slots:
            if fill.side == OrderSide.BUY and slot.buy_price == fill.price:
                fill.slot_index = slot.index
                return slot
            if fill.side == OrderSide.SELL and slot.sell_price == fill.price:
                fill.slot_index = slot.index
                return slot
        return None

    def _touch_daily(self, runtime: BotRuntime, fill: Fill) -> None:
        day = fill.ts.date().isoformat()
        if runtime.daily_realized_date != day:
            runtime.daily_realized_date = day
            runtime.daily_realized = ZERO
        if fill.side == OrderSide.SELL:
            # Approximate: realized increment already applied on the slot.
            pass

    def gap_index(self, runtime: BotRuntime, mark: Decimal) -> int:
        if not runtime.prices:
            return 0
        return mark_gap_index(runtime.prices, mark)
