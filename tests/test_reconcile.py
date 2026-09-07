from __future__ import annotations

from decimal import Decimal

import pytest

from aethergrid.domain.enums import OrderSide, OrderStatus
from aethergrid.domain.models import Order
from aethergrid.exchange.base import Exchange
from aethergrid.exchange.coinbase import reconcile_orders


class DummyExchange(Exchange):
    venue = None  # type: ignore[assignment]

    def __init__(self, remote: list[Order], filled: dict[str, Order] | None = None) -> None:
        self.remote = remote
        self.filled = filled or {}

    async def list_products(self):
        return []

    async def get_product(self, product_id: str):
        raise NotImplementedError

    async def get_ticker(self, product_id: str):
        raise NotImplementedError

    async def get_candles(self, *a, **k):
        return []

    async def get_balances(self):
        return []

    async def fee_rates(self):
        raise NotImplementedError

    async def preview_limit(self, **k):
        return {}

    async def place_limit(self, **k):
        raise NotImplementedError

    async def place_ioc(self, **k):
        raise NotImplementedError

    async def cancel(self, order_id: str) -> None:
        return None

    async def cancel_many(self, order_ids: list[str]) -> None:
        return None

    async def get_order(self, order_id: str) -> Order | None:
        return self.filled.get(order_id)

    async def list_open_orders(self, product_id: str | None = None):
        return list(self.remote)

    async def list_fills(self, **k):
        return []


def _order(cid: str, status: OrderStatus = OrderStatus.OPEN, ex: str | None = None) -> Order:
    return Order(
        client_order_id=cid,
        exchange_order_id=ex or cid,
        product_id="BTC-USD",
        side=OrderSide.BUY,
        price=Decimal("100"),
        size=Decimal("1"),
        status=status,
    )


@pytest.mark.asyncio
async def test_reconcile_detects_missing_and_unknown() -> None:
    local = {"ag_local": _order("ag_local")}
    remote = [_order("ag_unknown_on_venue")]
    filled = {"ag_local": _order("ag_local", OrderStatus.FILLED)}
    ex = DummyExchange(remote, filled)
    report = await reconcile_orders(ex, local)
    assert "ag_local" in report["filled_elsewhere"] or "ag_local" in report["missing_on_venue"]
    assert "ag_unknown_on_venue" in report["unknown_on_venue"]


@pytest.mark.asyncio
async def test_reconcile_status_mismatch() -> None:
    local = {"ag_1": _order("ag_1")}
    remote = [_order("ag_1")]
    remote[0].filled_size = Decimal("0.5")
    remote[0].status = OrderStatus.PARTIAL
    report = await reconcile_orders(DummyExchange(remote), local)
    assert "ag_1" in report["status_mismatch"]
