from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from aethergrid.domain.enums import OrderSide, OrderStatus, OrderType, TimeInForce, Venue
from aethergrid.domain.models import Balance, Candle, FeeRates, Fill, Order, Product, Ticker
from aethergrid.exchange.base import Exchange, TickerCallback, UserCallback
from aethergrid.logging import get_logger
from aethergrid.market.products import PublicMarket
from aethergrid.money import D, ZERO, fee_from_notional

log = get_logger("paper")


class PaperExchange(Exchange):
    """Simulated venue. Limit fills when last/mid crosses the resting price.

    Shares the GridEngine state machine with live; only the fill source differs.
    """

    venue = Venue.PAPER

    def __init__(
        self,
        market: PublicMarket,
        *,
        quote_currency: str = "USD",
        starting_quote: Decimal = Decimal("100000"),
        fee_bps: Decimal = Decimal("60"),
        extra_balances: dict[str, Decimal] | None = None,
        venue: Venue | None = None,
    ) -> None:
        self.market = market
        self.venue = venue or Venue.PAPER
        self.quote_currency = quote_currency.upper()
        self._fee_rate = (fee_bps / Decimal("10000")) if fee_bps else Decimal("0.006")
        self._balances: dict[str, Decimal] = {self.quote_currency: starting_quote}
        if extra_balances:
            for k, v in extra_balances.items():
                self._balances[k.upper()] = D(v)
        self._holds: dict[str, Decimal] = {}
        self._orders: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._last_tick: dict[str, Ticker] = {}
        self._user_callbacks: list[UserCallback] = []
        self._ticker_callbacks: list[TickerCallback] = []

    def seed_balance(self, currency: str, amount: Decimal) -> None:
        self._balances[currency.upper()] = amount

    def virtual_balances(self) -> dict[str, Decimal]:
        return dict(self._balances)

    async def list_products(self) -> list[Product]:
        return await self.market.list_products()

    async def get_product(self, product_id: str) -> Product:
        return await self.market.get_product(product_id)

    async def get_ticker(self, product_id: str) -> Ticker:
        ticker = await self.market.get_ticker(product_id)
        self._last_tick[product_id] = ticker
        return ticker

    async def get_candles(
        self,
        product_id: str,
        granularity: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 300,
    ) -> list[Candle]:
        return await self.market.get_candles(product_id, granularity, start, end, limit)

    async def get_balances(self) -> list[Balance]:
        currencies = set(self._balances) | set(self._holds)
        out: list[Balance] = []
        for ccy in sorted(currencies):
            hold = self._holds.get(ccy, ZERO)
            available = self._balances.get(ccy, ZERO)
            out.append(Balance(currency=ccy, available=max(ZERO, available - hold), hold=hold))
        return out

    async def fee_rates(self) -> FeeRates:
        return FeeRates(maker=self._fee_rate, taker=self._fee_rate)

    async def preview_limit(
        self,
        *,
        product_id: str,
        side: OrderSide,
        price: Decimal,
        size: Decimal,
    ) -> dict[str, object]:
        notion = price * size
        fee = fee_from_notional(notion, self._fee_rate)
        return {
            "commission_total": str(fee),
            "best_bid": str(self._last_tick.get(product_id).bid if product_id in self._last_tick else 0),
            "best_ask": str(self._last_tick.get(product_id).ask if product_id in self._last_tick else 0),
            "errs": [],
        }

    async def place_limit(
        self,
        *,
        client_order_id: str,
        product_id: str,
        side: OrderSide,
        price: Decimal,
        size: Decimal,
        tif: TimeInForce = TimeInForce.GTC,
        post_only: bool = False,
    ) -> Order:
        if client_order_id in self._orders and self._orders[client_order_id].is_open:
            return self._orders[client_order_id]
        product = await self.get_product(product_id)
        base, quote = product.base_currency, product.quote_currency
        if size <= 0 or price <= 0:
            raise ValueError("price and size must be positive")
        order = Order(
            client_order_id=client_order_id,
            product_id=product_id,
            side=side,
            price=price,
            size=size,
            status=OrderStatus.OPEN,
            order_type=OrderType.LIMIT,
            tif=tif,
            exchange_order_id=f"{self.venue.value}_{uuid4().hex[:12]}",
            venue=self.venue,
        )
        if side == OrderSide.BUY:
            need = price * size + fee_from_notional(price * size, self._fee_rate)
            avail = self._available(quote)
            if avail < need:
                order.status = OrderStatus.FAILED
                order.error = f"insufficient {quote}: {avail} < {need}"
                self._orders[client_order_id] = order
                raise ValueError(order.error)
            self._holds[quote] = self._holds.get(quote, ZERO) + need
        else:
            avail = self._available(base)
            if avail < size:
                order.status = OrderStatus.FAILED
                order.error = f"insufficient {base}: {avail} < {size}"
                self._orders[client_order_id] = order
                raise ValueError(order.error)
            self._holds[base] = self._holds.get(base, ZERO) + size

        self._orders[client_order_id] = order
        if tif == TimeInForce.IOC:
            ticker = self._last_tick.get(product_id) or await self.get_ticker(product_id)
            await self._try_fill_order(order, ticker, aggressive=True)
        log.info(
            "paper_place",
            client_order_id=client_order_id,
            product_id=product_id,
            side=side,
            price=str(price),
            size=str(size),
        )
        return order

    async def place_ioc(
        self,
        *,
        client_order_id: str,
        product_id: str,
        side: OrderSide,
        size: Decimal,
        price: Decimal | None = None,
    ) -> Order:
        ticker = self._last_tick.get(product_id) or await self.get_ticker(product_id)
        px = price or ticker.last or ticker.mid
        if side == OrderSide.SELL:
            px = ticker.bid or px
        else:
            px = ticker.ask or px
        return await self.place_limit(
            client_order_id=client_order_id,
            product_id=product_id,
            side=side,
            price=px,
            size=size,
            tif=TimeInForce.IOC,
        )

    async def cancel(self, order_id: str) -> None:
        order = self._find(order_id)
        if not order or not order.is_open:
            return
        self._release_hold(order, order.remaining)
        order.status = OrderStatus.CANCELLED
        order.updated_at = datetime.now(UTC)

    async def cancel_many(self, order_ids: list[str]) -> None:
        for oid in order_ids:
            await self.cancel(oid)

    async def get_order(self, order_id: str) -> Order | None:
        return self._find(order_id)

    async def list_open_orders(self, product_id: str | None = None) -> list[Order]:
        out = [o for o in self._orders.values() if o.is_open]
        if product_id:
            out = [o for o in out if o.product_id == product_id]
        return out

    async def list_fills(
        self,
        *,
        product_id: str | None = None,
        order_id: str | None = None,
    ) -> list[Fill]:
        fills = self._fills
        if product_id:
            fills = [f for f in fills if f.product_id == product_id]
        if order_id:
            fills = [
                f
                for f in fills
                if f.client_order_id == order_id or f.exchange_order_id == order_id
            ]
        return list(fills)

    async def on_ticker(self, ticker: Ticker) -> list[Fill]:
        """Drive simulated fills from a public (or injected) ticker."""
        self._last_tick[ticker.product_id] = ticker
        fills: list[Fill] = []
        for order in list(self._orders.values()):
            if not order.is_open or order.product_id != ticker.product_id:
                continue
            got = await self._try_fill_order(order, ticker, aggressive=False)
            fills.extend(got)
        for cb in self._ticker_callbacks:
            maybe = cb(ticker)
            if hasattr(maybe, "__await__"):
                await maybe  # type: ignore[misc]
        return fills

    def inject_ticker(self, ticker: Ticker) -> None:
        self._last_tick[ticker.product_id] = ticker

    async def subscribe_tickers(
        self,
        product_ids: list[str],
        callback: TickerCallback,
    ) -> None:
        self._ticker_callbacks.append(callback)
        _ = product_ids

    async def subscribe_user(self, callback: UserCallback) -> None:
        self._user_callbacks.append(callback)

    async def close(self) -> None:
        await self.market.close()

    # ----- fill mechanics -------------------------------------------------

    async def _try_fill_order(
        self,
        order: Order,
        ticker: Ticker,
        *,
        aggressive: bool,
    ) -> list[Fill]:
        last = ticker.last or ticker.mid
        mid = ticker.mid or last
        crossed = False
        if order.side == OrderSide.BUY:
            crossed = last <= order.price or mid <= order.price
            if aggressive:
                crossed = True
        else:
            crossed = last >= order.price or mid >= order.price
            if aggressive:
                crossed = True
        if not crossed:
            return []
        qty = order.remaining
        if qty <= 0:
            return []
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

    def _apply_fill(self, order: Order, fill: Fill) -> None:
        product_id = order.product_id
        base, quote = product_id.split("-", 1)
        order.filled_size += fill.size
        order.filled_value += fill.price * fill.size
        order.fee += fill.fee
        order.updated_at = datetime.now(UTC)
        remaining_after = order.size - order.filled_size
        if remaining_after <= ZERO:
            order.status = OrderStatus.FILLED
        else:
            order.status = OrderStatus.PARTIAL

        if order.side == OrderSide.BUY:
            reserved = order.price * fill.size + fee_from_notional(
                order.price * fill.size, self._fee_rate
            )
            self._holds[quote] = max(ZERO, self._holds.get(quote, ZERO) - reserved)
            spend = fill.price * fill.size + fill.fee
            self._balances[quote] = self._balances.get(quote, ZERO) - spend
            self._balances[base] = self._balances.get(base, ZERO) + fill.size
        else:
            self._holds[base] = max(ZERO, self._holds.get(base, ZERO) - fill.size)
            self._balances[base] = self._balances.get(base, ZERO) - fill.size
            self._balances[quote] = self._balances.get(quote, ZERO) + fill.price * fill.size - fill.fee

        if order.status == OrderStatus.CANCELLED:
            return
        if remaining_after <= ZERO:
            # Release leftover hold if price/fee reserved more than spent.
            self._release_hold(order, ZERO)

    def _available(self, currency: str) -> Decimal:
        return self._balances.get(currency, ZERO) - self._holds.get(currency, ZERO)

    def _release_hold(self, order: Order, remaining_size: Decimal) -> None:
        base, quote = order.product_id.split("-", 1)
        if order.side == OrderSide.BUY:
            leftover = order.price * remaining_size + fee_from_notional(
                order.price * remaining_size, self._fee_rate
            )
            self._holds[quote] = max(ZERO, self._holds.get(quote, ZERO) - leftover)
        else:
            self._holds[base] = max(ZERO, self._holds.get(base, ZERO) - remaining_size)

    def _find(self, order_id: str) -> Order | None:
        if order_id in self._orders:
            return self._orders[order_id]
        for order in self._orders.values():
            if order.exchange_order_id == order_id:
                return order
        return None
