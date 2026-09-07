from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from aethergrid.domain.models import BotRuntime, Candle, Product, Ticker
from aethergrid.market.volatility import range_quality, realized_vol
from aethergrid.money import ZERO
from aethergrid.strategy.ladder import fee_spread_cover_ok, min_step_pct
from aethergrid.strategy.protection import atr, donchian, efficiency_ratio, inventory_skew


class PairFeatures(BaseModel):
    product_id: str
    quote: str
    mark: Decimal = ZERO
    spread_bps: Decimal = ZERO
    volume_24h: Decimal = ZERO
    vol_1h: Decimal = ZERO
    vol_4h: Decimal = ZERO
    vol_1d: Decimal = ZERO
    atr_1h: Decimal = ZERO
    er: Decimal = ZERO
    donchian_high: Decimal = ZERO
    donchian_low: Decimal = ZERO
    oscillation: Decimal = ZERO
    inside_frac: Decimal = ZERO
    range_low: Decimal = ZERO
    range_high: Decimal = ZERO
    price_inside_range: bool = False
    step_ok: bool = False
    liquidity_ok: bool = False
    usd_like: bool = False
    trend_regime: bool = False
    score: Decimal = ZERO


class BotFeatures(BaseModel):
    bot_id: str
    product_id: str
    status: str
    mark: Decimal = ZERO
    inventory_skew: Decimal = ZERO
    realized: Decimal = ZERO
    unrealized: Decimal = ZERO
    equity: Decimal = ZERO
    open_orders: int = 0
    error_count: int = 0
    entries_paused: bool = False
    breakout: bool = False
    in_range: bool = True


class OperatorFeatures(BaseModel):
    pairs: list[PairFeatures] = Field(default_factory=list)
    bots: list[BotFeatures] = Field(default_factory=list)
    equity: Decimal = ZERO
    cash: Decimal = ZERO
    daily_realized: Decimal = ZERO
    kill_switch: bool = False
    mode: str = "paper"
    errors: list[str] = Field(default_factory=list)


def pair_features(
    product: Product,
    ticker: Ticker,
    candles_1h: list[Candle],
    candles_1d: list[Candle],
    fee_rate: Decimal,
) -> PairFeatures:
    mark = ticker.last or ticker.mid or product.price
    rq = range_quality(candles_1d or candles_1h, period=30)
    high = rq["high"] or (donchian(candles_1d, 30)[0] if candles_1d else ZERO)
    low = rq["low"] or (donchian(candles_1d, 30)[1] if candles_1d else ZERO)
    inside = bool(low and high and low < mark < high)
    # Hypothetical 2% geometric step over the swing range.
    step_ok = False
    if low > 0 and high > low:
        from aethergrid.strategy.ladder import geometric_prices

        try:
            prices = geometric_prices(low, high, 21)
            spread_pct = ticker.spread_bps / Decimal("10000") if ticker.spread_bps else Decimal("0.0005")
            step_ok = fee_spread_cover_ok(prices, fee_rate, spread_pct, Decimal("2"))
        except Exception:  # noqa: BLE001
            step_ok = min_step_pct([low, high]) > fee_rate * 4
    usd_like = product.quote_currency.upper() in {"USD", "USDC"}
    volume_ok = product.volume_24h > 0 or ticker.volume_24h > 0
    # Prefer high notionals: volume_24h on Coinbase product is often base units; use price*vol when possible.
    liq = ticker.volume_24h or product.volume_24h
    liquidity_ok = usd_like and (liq > 0 or True) and ticker.spread_bps < Decimal("30")
    er = efficiency_ratio(candles_1h or candles_1d, 20)
    trend = er >= Decimal("0.45")
    vol_1h = realized_vol(candles_1h[-24:] if candles_1h else [], 24)
    vol_4h = realized_vol(candles_1h[-16:] if candles_1h else [], 16)
    vol_1d = realized_vol(candles_1d[-30:] if candles_1d else [], 30)
    score = ZERO
    if usd_like:
        score += Decimal("2")
    if inside:
        score += Decimal("2")
    if step_ok:
        score += Decimal("2")
    if rq["oscillation"] >= Decimal("0.15"):
        score += Decimal("2")
    else:
        score -= Decimal("1")
    if trend:
        score -= Decimal("2")
    if ticker.spread_bps > Decimal("15"):
        score -= Decimal("1")
    if volume_ok:
        score += Decimal("1")
    return PairFeatures(
        product_id=product.product_id,
        quote=product.quote_currency,
        mark=mark,
        spread_bps=ticker.spread_bps,
        volume_24h=liq,
        vol_1h=vol_1h,
        vol_4h=vol_4h,
        vol_1d=vol_1d,
        atr_1h=atr(candles_1h, 14),
        er=er,
        donchian_high=high,
        donchian_low=low,
        oscillation=rq["oscillation"],
        inside_frac=rq["inside_frac"],
        range_low=low,
        range_high=high,
        price_inside_range=inside,
        step_ok=step_ok,
        liquidity_ok=liquidity_ok,
        usd_like=usd_like,
        trend_regime=trend,
        score=score,
    )


def bot_features(runtime: BotRuntime) -> BotFeatures:
    mark = runtime.last_mark or ZERO
    equity = runtime.inventory.equity(mark) if mark else runtime.inventory.quote
    in_range = True
    if mark and runtime.config.lower_price and runtime.config.upper_price:
        in_range = runtime.config.lower_price <= mark <= runtime.config.upper_price
    return BotFeatures(
        bot_id=runtime.bot_id,
        product_id=runtime.product_id,
        status=runtime.status.value,
        mark=mark,
        inventory_skew=inventory_skew(runtime),
        realized=runtime.inventory.realized_pnl,
        unrealized=equity - runtime.inventory.starting_equity - runtime.inventory.realized_pnl,
        equity=equity,
        open_orders=sum(1 for o in runtime.open_orders.values() if o.is_open),
        error_count=runtime.error_count,
        entries_paused=runtime.protection.entries_paused,
        breakout=runtime.protection.breakout_active,
        in_range=in_range,
    )
