from __future__ import annotations

from decimal import Decimal

import pytest

from aethergrid.domain.enums import OrderSide, OrderStatus, Venue
from aethergrid.domain.models import Ticker
from aethergrid.exchange.demo import DemoExchange, DemoMarket


@pytest.fixture
def demo() -> DemoExchange:
    return DemoExchange(starting_quote=Decimal("10000"), seed=1, partial_p=0.0)


@pytest.mark.asyncio
async def test_demo_catalog_looks_like_coinbase_spot(demo: DemoExchange) -> None:
    products = await demo.list_products()
    ids = {p.product_id for p in products}
    assert "BTC-USD" in ids and "ETH-USD" in ids and "SOL-USD" in ids
    assert 8 <= len(products) <= 12
    btc = await demo.get_product("BTC-USD")
    assert btc.tradable
    assert btc.quote_increment > 0
    assert btc.base_increment > 0
    assert btc.min_market_funds > 0
    assert demo.venue == Venue.DEMO


@pytest.mark.asyncio
async def test_limit_buy_fills_when_synthetic_last_crosses(demo: DemoExchange) -> None:
    ticker = await demo.get_ticker("BTC-USD")
    order = await demo.place_limit(
        client_order_id="d1",
        product_id="BTC-USD",
        side=OrderSide.BUY,
        price=ticker.last,
        size=Decimal("0.001"),
    )
    assert order.status == OrderStatus.OPEN
    assert order.venue == Venue.DEMO
    fills = await demo.on_ticker(
        Ticker(
            product_id="BTC-USD",
            price=ticker.last - Decimal("10"),
            bid=ticker.last - Decimal("11"),
            ask=ticker.last - Decimal("9"),
        )
    )
    assert fills
    assert fills[0].side == OrderSide.BUY
    assert fills[0].venue == Venue.DEMO
    assert order.status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_partial_fills_sometimes() -> None:
    demo = DemoExchange(starting_quote=Decimal("10000"), seed=3, partial_p=1.0)
    ticker = await demo.get_ticker("ETH-USD")
    order = await demo.place_limit(
        client_order_id="p1",
        product_id="ETH-USD",
        side=OrderSide.BUY,
        price=ticker.last,
        size=Decimal("0.05"),
    )
    fills = await demo.on_ticker(
        Ticker(product_id="ETH-USD", price=ticker.last, bid=ticker.last, ask=ticker.last)
    )
    assert fills
    assert fills[0].size < Decimal("0.05")
    assert order.status == OrderStatus.PARTIAL


@pytest.mark.asyncio
async def test_demo_market_never_needs_network() -> None:
    market = DemoMarket(seed=0)
    ticker = await market.get_ticker("SOL-USD")
    candles = await market.get_candles("SOL-USD", "ONE_HOUR", limit=20)
    assert ticker.price > 0
    assert candles
    await market.close()
