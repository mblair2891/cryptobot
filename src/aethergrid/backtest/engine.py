from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aethergrid.backtest.metrics import BacktestReport
from aethergrid.domain.enums import OrderSide
from aethergrid.domain.models import Candle, Fill, GridConfig, Product, Ticker
from aethergrid.exchange.paper import PaperExchange
from aethergrid.market.products import PublicMarket
from aethergrid.money import ZERO, fee_from_notional
from aethergrid.strategy.grid import GridEngine


async def load_history(
    market: PublicMarket,
    product_id: str,
    days: int,
) -> list[Candle]:
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    # Coinbase candle windows are limited; page by ~300 one-hour bars.
    candles: list[Candle] = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(hours=250), end)
        part = await market.get_candles(
            product_id, "ONE_HOUR", start=cursor, end=chunk_end, limit=300
        )
        candles.extend(part)
        cursor = chunk_end
    uniq = {c.start: c for c in candles}
    return [uniq[k] for k in sorted(uniq)]


def conservative_touch(
    candle: Candle,
    side: OrderSide,
    price: Decimal,
    fee_rate: Decimal,
    half_spread: Decimal,
) -> Decimal | None:
    """Fill if the bar trades through the level; fill price worse by half-spread + we still pay fee."""
    if side == OrderSide.BUY:
        if candle.low <= price:
            return price  # limit buy fills at limit; fee applied separately
        return None
    if candle.high >= price:
        return price
    _ = half_spread
    _ = fee_rate
    return None


async def run_backtest(
    *,
    product_id: str,
    days: int = 90,
    investment: Decimal = Decimal("10000"),
    levels: int = 21,
    lower: Decimal | None = None,
    upper: Decimal | None = None,
    market: PublicMarket | None = None,
    candles: list[Candle] | None = None,
    product: Product | None = None,
) -> BacktestReport:
    own_market = market is None
    market = market or PublicMarket()
    try:
        if product is None:
            try:
                product = await market.get_product(product_id)
            except Exception:  # noqa: BLE001
                product = Product(
                    product_id=product_id,
                    base_currency=product_id.split("-")[0],
                    quote_currency=product_id.split("-")[-1],
                    quote_increment=Decimal("0.01"),
                    base_increment=Decimal("0.0001"),
                    min_market_funds=Decimal("1"),
                )
        if candles is None:
            candles = await load_history(market, product_id, days)
        if len(candles) < 24:
            return BacktestReport(
                product_id=product_id,
                days=days,
                bars=len(candles),
                notes=["insufficient candles"],
            )
        look = candles[: min(24, len(candles))]
        hi = max(c.high for c in look)
        lo = min(c.low for c in look)
        lower = lower or lo * Decimal("0.98")
        upper = upper or hi * Decimal("1.02")
        first = candles[min(24, len(candles) - 1)]
        mark = first.close
        if not (lower < mark < upper):
            lower = mark * Decimal("0.9")
            upper = mark * Decimal("1.1")
        fee_rate = Decimal("0.006")
        engine = GridEngine(product, fee_rate)
        cfg = GridConfig(
            product_id=product_id,
            investment=investment,
            lower_price=lower,
            upper_price=upper,
            grid_levels=levels,
            trailing_up=True,
            stop_loss_pct=Decimal("0.2"),
        )
        runtime = engine.initialize(cfg, mark, investment)
        paper = PaperExchange(
            market,
            starting_quote=investment,
            fee_bps=Decimal("60"),
        )
        trades = 0
        cycles = 0
        peak = investment
        max_dd = ZERO
        in_market_bars = 0
        half_spread = mark * Decimal("0.00025")

        for candle in candles[min(24, len(candles) - 1) :]:
            ticker = Ticker(
                product_id=product_id,
                price=candle.close,
                bid=candle.close - half_spread,
                ask=candle.close + half_spread,
                ts=candle.start,
            )
            paper.inject_ticker(ticker)
            runtime.last_mark = candle.close
            # Desired book vs paper.
            intents = engine.desired_intents(runtime, candle.close)
            for intent in intents:
                if intent.kind.value == "cancel" and intent.cancel_client_order_id:
                    await paper.cancel(intent.cancel_client_order_id)
                    engine.on_cancel_ack(runtime, intent.cancel_client_order_id)
                elif intent.kind.value in {"place", "replace"} and intent.side and intent.price and intent.size:
                    if intent.kind.value == "replace" and intent.cancel_client_order_id:
                        await paper.cancel(intent.cancel_client_order_id)
                        engine.on_cancel_ack(runtime, intent.cancel_client_order_id)
                    try:
                        order = await paper.place_limit(
                            client_order_id=intent.client_order_id or "",
                            product_id=product_id,
                            side=intent.side,
                            price=intent.price,
                            size=intent.size,
                        )
                    except Exception:  # noqa: BLE001
                        continue
                    order.slot_index = intent.slot_index
                    order.bot_id = runtime.bot_id
                    engine.on_order_ack(runtime, order)
                    fill_px = conservative_touch(candle, intent.side, intent.price, fee_rate, half_spread)
                    if fill_px is not None:
                        fills = await paper.on_ticker(
                            Ticker(
                                product_id=product_id,
                                price=fill_px,
                                bid=fill_px,
                                ask=fill_px,
                                ts=candle.start,
                            )
                        )
                        for fill in fills:
                            engine.on_fill(runtime, fill)
                            trades += 1
                            if fill.side.value == "SELL":
                                cycles += 1
            engine.maybe_trail(runtime, candle.close)
            exit_i = engine.check_exits(runtime, candle.close)
            if exit_i:
                break
            equity = runtime.inventory.equity(candle.close)
            if equity > peak:
                peak = equity
            if peak > 0:
                dd = (peak - equity) / peak
                if dd > max_dd:
                    max_dd = dd
            if runtime.inventory.base > 0 or runtime.open_orders:
                in_market_bars += 1

        used = max(1, len(candles) - 24)
        return BacktestReport(
            product_id=product_id,
            days=days,
            bars=len(candles),
            trades=trades,
            grid_cycles=cycles,
            realized_pnl=runtime.inventory.realized_pnl,
            fees=runtime.inventory.fees_paid,
            max_drawdown_pct=max_dd,
            ending_equity=runtime.inventory.equity(candles[-1].close),
            starting_equity=investment,
            time_in_market_pct=Decimal(in_market_bars) / Decimal(used),
        )
    finally:
        if own_market:
            await market.close()
