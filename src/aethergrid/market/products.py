from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from aethergrid.domain.models import Candle, Product, Ticker
from aethergrid.logging import get_logger
from aethergrid.money import D, ZERO

log = get_logger("market")

PUBLIC_BASE = "https://api.coinbase.com/api/v3/brokerage/market"
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

PREFERRED_QUOTES = ("USD", "USDC", "USDT", "EUR", "GBP")


def _product_from_payload(raw: dict[str, Any]) -> Product:
    quote_inc = D(raw.get("quote_increment") or raw.get("quoteIncrement") or "0.01")
    base_inc = D(raw.get("base_increment") or raw.get("baseIncrement") or "0.00000001")
    return Product(
        product_id=str(raw.get("product_id") or raw.get("id") or ""),
        base_currency=str(raw.get("base_currency_id") or raw.get("base_currency") or ""),
        quote_currency=str(raw.get("quote_currency_id") or raw.get("quote_currency") or ""),
        status=str(raw.get("status") or "online"),
        trading_disabled=bool(raw.get("trading_disabled", False)),
        product_type=str(raw.get("product_type") or "SPOT"),
        price=D(raw.get("price") or 0),
        quote_increment=quote_inc if quote_inc > 0 else Decimal("0.01"),
        base_increment=base_inc if base_inc > 0 else Decimal("0.00000001"),
        quote_min_size=D(raw.get("quote_min_size") or 0),
        base_min_size=D(raw.get("base_min_size") or 0),
        min_market_funds=D(raw.get("quote_min_size") or raw.get("min_market_funds") or 0),
        base_max_size=D(raw["base_max_size"]) if raw.get("base_max_size") else None,
        quote_max_size=D(raw["quote_max_size"]) if raw.get("quote_max_size") else None,
        cancel_only=bool(raw.get("cancel_only", False)),
        limit_only=bool(raw.get("limit_only", False)),
        post_only=bool(raw.get("post_only", False)),
        auction_mode=bool(raw.get("auction_mode", False)),
        volume_24h=D(raw.get("volume_24h") or raw.get("approximate_volume_24h") or 0),
        approximate_price=D(raw.get("price") or 0),
    )


def filter_spot(
    products: list[Product],
    *,
    quotes: tuple[str, ...] | None = None,
    online_only: bool = True,
) -> list[Product]:
    out: list[Product] = []
    for p in products:
        if online_only and not p.tradable:
            continue
        ptype = (p.product_type or "SPOT").upper()
        if ptype not in {"SPOT", ""}:
            continue
        if quotes and p.quote_currency.upper() not in {q.upper() for q in quotes}:
            continue
        if not p.product_id or "-" not in p.product_id:
            continue
        out.append(p)
    return out


class PublicMarket:
    """Unauthenticated Coinbase Advanced Trade market data. Paper mode uses this only."""

    def __init__(self, timeout: float = 20.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "aethergrid/1.0"})
        self._products: dict[str, Product] = {}
        self._products_at: datetime | None = None

    async def close(self) -> None:
        await self._client.aclose()

    async def list_products(self, force: bool = False) -> list[Product]:
        now = datetime.now(UTC)
        if (
            not force
            and self._products
            and self._products_at
            and now - self._products_at < timedelta(minutes=5)
        ):
            return list(self._products.values())
        resp = await self._client.get(f"{PUBLIC_BASE}/products")
        resp.raise_for_status()
        payload = resp.json()
        items = payload.get("products") or payload.get("data") or []
        products = [_product_from_payload(x) for x in items if isinstance(x, dict)]
        products = [p for p in products if p.product_id]
        self._products = {p.product_id: p for p in products}
        self._products_at = now
        log.info("products_loaded", count=len(products), tradable=len(filter_spot(products)))
        return products

    async def get_product(self, product_id: str) -> Product:
        if product_id not in self._products:
            await self.list_products()
        if product_id in self._products:
            # Refresh price via ticker if stale product.
            return self._products[product_id]
        resp = await self._client.get(f"{PUBLIC_BASE}/products/{product_id}")
        resp.raise_for_status()
        product = _product_from_payload(resp.json())
        self._products[product_id] = product
        return product

    async def get_ticker(self, product_id: str) -> Ticker:
        resp = await self._client.get(f"{PUBLIC_BASE}/products/{product_id}/ticker")
        if resp.status_code == 404:
            # Fallback: product price + book.
            product = await self.get_product(product_id)
            book = await self.get_best_bid_ask(product_id)
            return Ticker(
                product_id=product_id,
                price=product.price or book.get("mid", ZERO),
                bid=book.get("bid", ZERO),
                ask=book.get("ask", ZERO),
            )
        resp.raise_for_status()
        data = resp.json()
        trades = data.get("trades") or []
        price = D(data.get("price") or 0)
        if not price and trades:
            price = D(trades[0].get("price") or 0)
        bid = D(data.get("best_bid") or data.get("bid") or 0)
        ask = D(data.get("best_ask") or data.get("ask") or 0)
        if bid <= 0 or ask <= 0:
            book = await self.get_best_bid_ask(product_id)
            bid = bid or book.get("bid", ZERO)
            ask = ask or book.get("ask", ZERO)
        if price <= 0:
            price = (bid + ask) / 2 if bid and ask else bid or ask
        return Ticker(
            product_id=product_id,
            price=price,
            bid=bid,
            ask=ask,
            volume_24h=D(data.get("volume_24h") or 0),
        )

    async def get_best_bid_ask(self, product_id: str) -> dict[str, Decimal]:
        resp = await self._client.get(
            f"{PUBLIC_BASE}/product_book",
            params={"product_id": product_id, "limit": 1},
        )
        resp.raise_for_status()
        data = resp.json()
        book = data.get("pricebook") or data
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        bid = D(bids[0]["price"]) if bids else ZERO
        ask = D(asks[0]["price"]) if asks else ZERO
        mid = (bid + ask) / 2 if bid and ask else bid or ask
        return {"bid": bid, "ask": ask, "mid": mid}

    async def get_candles(
        self,
        product_id: str,
        granularity: str = "ONE_HOUR",
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 300,
    ) -> list[Candle]:
        gran = granularity.upper()
        seconds = GRANULARITY_SECONDS.get(gran, 3600)
        end = end or datetime.now(UTC)
        start = start or (end - timedelta(seconds=seconds * limit))
        resp = await self._client.get(
            f"{PUBLIC_BASE}/products/{product_id}/candles",
            params={
                "start": str(int(start.timestamp())),
                "end": str(int(end.timestamp())),
                "granularity": gran,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("candles") or []
        candles: list[Candle] = []
        for c in raw:
            ts = c.get("start") or c.get("time")
            if ts is None:
                continue
            start_dt = datetime.fromtimestamp(int(ts), tz=UTC)
            candles.append(
                Candle(
                    product_id=product_id,
                    start=start_dt,
                    open=D(c.get("open") or 0),
                    high=D(c.get("high") or 0),
                    low=D(c.get("low") or 0),
                    close=D(c.get("close") or 0),
                    volume=D(c.get("volume") or 0),
                    granularity=gran,
                )
            )
        candles.sort(key=lambda x: x.start)
        return candles[-limit:]
