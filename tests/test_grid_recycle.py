from __future__ import annotations

from decimal import Decimal

from aethergrid.domain.enums import IntentKind, OrderSide, SlotState
from aethergrid.domain.models import Fill, GridConfig
from aethergrid.strategy.grid import GridEngine


def test_starting_status_still_places_buys(engine: GridEngine, grid_config: GridConfig) -> None:
    from aethergrid.domain.enums import BotStatus

    runtime = engine.initialize(grid_config, Decimal("100000"), Decimal("10000"))
    runtime.status = BotStatus.STARTING
    intents = engine.desired_intents(runtime, Decimal("100000"))
    assert any(i.kind == IntentKind.PLACE and i.side == OrderSide.BUY for i in intents)


def test_initial_buys_below_mark_no_sells_without_inventory(
    engine: GridEngine, grid_config: GridConfig
) -> None:
    runtime = engine.initialize(grid_config, Decimal("100000"), Decimal("10000"))
    intents = engine.desired_intents(runtime, Decimal("100000"))
    places = [i for i in intents if i.kind == IntentKind.PLACE]
    assert places
    assert all(i.side == OrderSide.BUY for i in places)
    assert runtime.inventory.base == 0
    assert all(s.held_base == 0 or s.buy_price < Decimal("100000") for s in runtime.slots)


def test_buy_fill_places_paired_sell(engine: GridEngine, grid_config: GridConfig) -> None:
    runtime = engine.initialize(grid_config, Decimal("100000"), Decimal("10000"))
    intents = engine.desired_intents(runtime, Decimal("100000"))
    buy = next(i for i in intents if i.kind == IntentKind.PLACE and i.side == OrderSide.BUY)
    from aethergrid.domain.enums import OrderStatus
    from aethergrid.domain.models import Order

    order = Order(
        client_order_id=buy.client_order_id or "x",
        product_id="BTC-USD",
        side=OrderSide.BUY,
        price=buy.price or 0,
        size=buy.size or 0,
        status=OrderStatus.OPEN,
        slot_index=buy.slot_index,
        bot_id=runtime.bot_id,
    )
    engine.on_order_ack(runtime, order)
    fill = Fill(
        client_order_id=order.client_order_id,
        product_id="BTC-USD",
        side=OrderSide.BUY,
        price=order.price,
        size=order.size,
        slot_index=order.slot_index,
        bot_id=runtime.bot_id,
    )
    engine.on_fill(runtime, fill)
    slot = runtime.slots[buy.slot_index or 0]
    assert slot.held_base > 0
    assert slot.state in {SlotState.SELL_OPEN, SlotState.BUY_OPEN}
    next_intents = engine.desired_intents(runtime, Decimal("100000"))
    sells = [i for i in next_intents if i.kind == IntentKind.PLACE and i.side == OrderSide.SELL]
    assert sells
    assert any(s.slot_index == slot.index and s.price == slot.sell_price for s in sells)


def test_sell_fill_reopens_buy(engine: GridEngine, grid_config: GridConfig) -> None:
    runtime = engine.initialize(grid_config, Decimal("100000"), Decimal("10000"))
    slot = next(s for s in runtime.slots if s.buy_price < Decimal("100000"))
    from aethergrid.domain.enums import OrderStatus
    from aethergrid.domain.models import Order

    buy = Order(
        client_order_id="b1",
        product_id="BTC-USD",
        side=OrderSide.BUY,
        price=slot.buy_price,
        size=slot.target_base,
        status=OrderStatus.OPEN,
        slot_index=slot.index,
    )
    engine.on_order_ack(runtime, buy)
    engine.on_fill(
        runtime,
        Fill(
            client_order_id="b1",
            product_id="BTC-USD",
            side=OrderSide.BUY,
            price=slot.buy_price,
            size=slot.target_base,
            slot_index=slot.index,
        ),
    )
    sell = Order(
        client_order_id="s1",
        product_id="BTC-USD",
        side=OrderSide.SELL,
        price=slot.sell_price,
        size=slot.held_base,
        status=OrderStatus.OPEN,
        slot_index=slot.index,
    )
    engine.on_order_ack(runtime, sell)
    engine.on_fill(
        runtime,
        Fill(
            client_order_id="s1",
            product_id="BTC-USD",
            side=OrderSide.SELL,
            price=slot.sell_price,
            size=sell.size,
            slot_index=slot.index,
        ),
    )
    assert slot.held_base == 0
    assert slot.state == SlotState.BUY_OPEN
    assert runtime.inventory.realized_pnl != 0
    assert runtime.cycles_completed == 1
    intents = engine.desired_intents(runtime, Decimal("100000"))
    buys = [i for i in intents if i.kind == IntentKind.PLACE and i.side == OrderSide.BUY]
    assert any(b.slot_index == slot.index for b in buys)


def test_never_sell_without_inventory(engine: GridEngine, grid_config: GridConfig) -> None:
    runtime = engine.initialize(grid_config, Decimal("100000"), Decimal("10000"))
    for slot in runtime.slots:
        slot.state = SlotState.SELL_OPEN
        slot.sell_remaining = slot.target_base
        slot.held_base = Decimal("0")
    intents = engine.desired_intents(runtime, Decimal("100000"))
    sells = [i for i in intents if i.side == OrderSide.SELL]
    assert sells == []
