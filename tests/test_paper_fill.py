from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from aethergrid.domain.enums import OrderSide, OrderStatus, TimeInForce
from aethergrid.domain.models import Product, Ticker
from aethergrid.exchange.paper import PaperExchange
from aethergrid.market.products import PublicMarket


class FakeMarket(PublicMarket):
    def __init__(self, product: Product) -> None:
        self._p = product

    async def list_products(self, force: bool = False):
        return [self._p]

    async def get_product(self, product_id: str):
        return self._p

    async def get_ticker(self, product_id: str):
        return Ticker(product_id=product_id, price=Decimal("100"), bid=Decimal("99.9"), ask=Decimal("100.1"))

    async def get_candles(self, *a, **k):
        return []

    async def close(self) -> None:
        return None


@pytest.fixture
def paper(product: Product) -> PaperExchange:
    cheap = product.model_copy(
        update={
            "product_id": "AAA-USD",
            "base_currency": "AAA",
            "price": Decimal("100"),
            "quote_increment": Decimal("0.01"),
            "base_increment": Decimal("0.001"),
            "base_min_size": Decimal("0.001"),
            "min_market_funds": Decimal("1"),
        }
    )
    return PaperExchange(FakeMarket(cheap), starting_quote=Decimal("10000"), fee_bps=Decimal("60"))


@pytest.mark.asyncio
async def test_limit_buy_fills_when_last_crosses(paper: PaperExchange) -> None:
    order = await paper.place_limit(
        client_order_id="c1",
        product_id="AAA-USD",
        side=OrderSide.BUY,
        price=Decimal("100"),
        size=Decimal("1"),
    )
    assert order.status == OrderStatus.OPEN
    fills = await paper.on_ticker(
        Ticker(product_id="AAA-USD", price=Decimal("99.5"), bid=Decimal("99.4"), ask=Decimal("99.6"))
    )
    assert fills
    assert fills[0].side == OrderSide.BUY
    assert order.status == OrderStatus.FILLED
    bals = {b.currency: b for b in await paper.get_balances()}
    assert bals["AAA"].total == Decimal("1")


@pytest.mark.asyncio
async def test_limit_sell_fills_when_last_crosses(paper: PaperExchange) -> None:
    paper.seed_balance("AAA", Decimal("2"))
    order = await paper.place_limit(
        client_order_id="s1",
        product_id="AAA-USD",
        side=OrderSide.SELL,
        price=Decimal("110"),
        size=Decimal("1"),
    )
    fills = await paper.on_ticker(
        Ticker(product_id="AAA-USD", price=Decimal("111"), bid=Decimal("110.5"), ask=Decimal("111.5"))
    )
    assert fills and fills[0].side == OrderSide.SELL
    assert order.status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_buy_does_not_fill_above_limit(paper: PaperExchange) -> None:
    await paper.place_limit(
        client_order_id="c2",
        product_id="AAA-USD",
        side=OrderSide.BUY,
        price=Decimal("90"),
        size=Decimal("1"),
        tif=TimeInForce.GTC,
    )
    fills = await paper.on_ticker(
        Ticker(
            product_id="AAA-USD",
            price=Decimal("100"),
            bid=Decimal("99.9"),
            ask=Decimal("100.1"),
            ts=datetime.now(UTC),
        )
    )
    assert fills == []
