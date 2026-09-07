from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aethergrid.demo.catalog import DEMO_PAIRS, DemoPair
from aethergrid.domain.enums import OrderSide, Venue
from aethergrid.domain.models import Candle, Fill, Order, Product, Ticker
from aethergrid.exchange.paper import PaperExchange
from aethergrid.logging import get_logger
from aethergrid.money import ZERO, D, fee_from_notional, quantize_down

log = get_logger("demo")

GRANULARITY_SECONDS = {
    "ONE_MINUTE": 60,
    "FIVE_MINUTE": 300,
    "FIFTEEN_MINUTE": 900,
    "THIRTY_MINUTE": 1800,
    "ONE_HOUR": 3600,
    "TWO_HOUR": 7200,
    "SIX_HOUR": 21600,
    "ONE_DAY": 86400,
}


def _product(spec: DemoPair, price: Decimal) -> Product:
    base, quote = spec.product_id.split("-", 1)
    return Product(
        product_id=spec.product_id,
        base_currency=base,
        quote_currency=quote,
        status="online",
        product_type="SPOT",
        price=price,
        quote_increment=spec.quote_increment,
        base_increment=spec.base_increment,
        base_min_size=spec.base_min_size,
        min_market_funds=spec.min_market_funds,
        volume_24h=spec.volume_24h,
        approximate_price=price,
    )


class DemoMarket:
    """In-process synthetic book. Zero network. No Coinbase client."""

    def __init__(self, *, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self._specs = {p.product_id: p for p in DEMO_PAIRS}
        self._price: dict[str, Decimal] = {p.product_id: p.mid for p in DEMO_PAIRS}
        self._anchor: dict[str, Decimal] = {p.product_id: p.mid for p in DEMO_PAIRS}
        self._shock_left: dict[str, int] = {p.product_id: 0 for p in DEMO_PAIRS}
        self._shock_mu: dict[str, float] = {p.product_id: 0.0 for p in DEMO_PAIRS}
        self._history: dict[str, list[Ticker]] = {p.product_id: [] for p in DEMO_PAIRS}
        self._t = datetime.now(UTC)
        self._warm()

    def _warm(self) -> None:
        for pid in self._specs:
            for _ in range(240):
                self._step_pair(pid, record=True, dt=0.004)

    def _step_pair(self, product_id: str, *, record: bool, dt: float = 0.0015) -> Ticker:
        spec = self._specs[product_id]
        px = float(self._price[product_id])
        if self._shock_left[product_id] > 0:
            self._shock_left[product_id] -= 1
            mu = self._shock_mu[product_id]
        else:
            mu = spec.mu
            if self._rng.random() < 0.012:
                self._shock_left[product_id] = self._rng.randint(4, 12)
                direction = -1.0 if spec.regime == "dump" else (1.0 if spec.regime == "pump" else self._rng.choice([-1.0, 1.0]))
                self._shock_mu[product_id] = direction * (0.8 + self._rng.random())
                mu = self._shock_mu[product_id]
        z = self._rng.gauss(0.0, 1.0)
        gbm = (mu - 0.5 * spec.sigma**2) * dt + spec.sigma * math.sqrt(dt) * z
        px *= math.exp(gbm)
        anchor = float(self._anchor[product_id])
        px += spec.kappa * (anchor - px) * dt * 40
        if px <= 0:
            px = float(spec.quote_increment) * 10
        price = D(str(round(px, 12)))
        tick = spec.quote_increment
        n = (price / tick).to_integral_value()
        price = n * tick
        if price <= 0:
            price = tick
        self._price[product_id] = price
        spread = max(tick, price * Decimal("0.0004"))
        bid = price - spread / 2
        ask = price + spread / 2
        ticker = Ticker(
            product_id=product_id,
            price=price,
            bid=max(tick, bid),
            ask=ask,
            volume_24h=spec.volume_24h,
            ts=self._t,
        )
        self._t += timedelta(seconds=1)
        if record:
            hist = self._history[product_id]
            hist.append(ticker)
            if len(hist) > 1500:
                del hist[: len(hist) - 1200]
        return ticker

    def snapshot(self, product_id: str) -> Ticker:
        if product_id not in self._specs:
            raise KeyError(product_id)
        hist = self._history[product_id]
        if hist:
            return hist[-1]
        return self._step_pair(product_id, record=True)

    async def list_products(self) -> list[Product]:
        return [_product(spec, self._price[spec.product_id]) for spec in DEMO_PAIRS]

    async def get_product(self, product_id: str) -> Product:
        spec = self._specs[product_id]
        return _product(spec, self._price[product_id])

    async def get_ticker(self, product_id: str) -> Ticker:
        if product_id not in self._specs:
            raise KeyError(product_id)
        return self._step_pair(product_id, record=True)

    async def get_candles(
        self,
        product_id: str,
        granularity: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 300,
    ) -> list[Candle]:
        _ = start, end
        hist = self._history.get(product_id) or []
        if not hist:
            return []
        seconds = GRANULARITY_SECONDS.get(granularity.upper(), 60)
        bucket = max(1, seconds // 60)
        candles: list[Candle] = []
        for i in range(0, len(hist), bucket):
            chunk = hist[i : i + bucket]
            if not chunk:
                continue
            prices = [t.price for t in chunk]
            candles.append(
                Candle(
                    product_id=product_id,
                    start=chunk[0].ts,
                    open=prices[0],
                    high=max(prices),
                    low=min(prices),
                    close=prices[-1],
                    volume=sum((t.volume_24h / Decimal("1440") for t in chunk), ZERO),
                    granularity=granularity.upper(),
                )
            )
        return candles[-limit:]

    async def close(self) -> None:
        return None


class DemoExchange(PaperExchange):
    """Simulated venue with synthetic prices. Never talks to Coinbase."""

    venue = Venue.DEMO

    def __init__(
        self,
        *,
        starting_quote: Decimal = Decimal("10000"),
        fee_bps: Decimal = Decimal("60"),
        seed: int = 42,
        partial_p: float = 0.25,
    ) -> None:
        market = DemoMarket(seed=seed)
        super().__init__(
            market,  # type: ignore[arg-type]
            quote_currency="USD",
            starting_quote=starting_quote,
            fee_bps=fee_bps,
            venue=Venue.DEMO,
        )
        self.partial_p = partial_p
        self._fill_rng = random.Random(seed + 99)
        log.info("demo_exchange_ready", pairs=len(DEMO_PAIRS), equity=str(starting_quote))

    async def _try_fill_order(
        self,
        order: Order,
        ticker: Ticker,
        *,
        aggressive: bool,
    ) -> list[Fill]:
        last = ticker.last or ticker.mid
        mid = ticker.mid or last
        if order.side == OrderSide.BUY:
            crossed = last <= order.price or mid <= order.price
        else:
            crossed = last >= order.price or mid >= order.price
        if aggressive:
            crossed = True
        if not crossed:
            return []
        qty = order.remaining
        if qty <= 0:
            return []
        if not aggressive and self._fill_rng.random() < self.partial_p:
            frac = Decimal(str(round(self._fill_rng.uniform(0.35, 0.8), 4)))
            sliced = quantize_down(qty * frac, Decimal("0.00000001"))
            if sliced > 0:
                qty = sliced
        fill_price = order.price
        fee = fee_from_notional(fill_price * qty, self._fee_rate)
        fill = Fill(
            client_order_id=order.client_order_id,
            exchange_order_id=order.exchange_order_id,
            product_id=order.product_id,
            side=order.side,
            price=fill_price,
            size=qty,
            fee=fee,
            bot_id=order.bot_id,
            slot_index=order.slot_index,
            venue=self.venue,
            liquidity="TAKER" if aggressive else "MAKER",
        )
        self._apply_fill(order, fill)
        self._fills.append(fill)
        for cb in self._user_callbacks:
            maybe = cb(fill)
            if hasattr(maybe, "__await__"):
                await maybe  # type: ignore[misc]
        return [fill]
