from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal

from aethergrid.domain.enums import OrderSide, TimeInForce, Venue
from aethergrid.domain.models import Balance, Candle, FeeRates, Fill, Order, Product, Ticker

TickerCallback = Callable[[Ticker], Awaitable[None] | None]
UserCallback = Callable[[Order | Fill], Awaitable[None] | None]


class Exchange(ABC):
    venue: Venue

    @abstractmethod
    async def list_products(self) -> list[Product]: ...

    @abstractmethod
    async def get_product(self, product_id: str) -> Product: ...

    @abstractmethod
    async def get_ticker(self, product_id: str) -> Ticker: ...

    @abstractmethod
    async def get_candles(
        self,
        product_id: str,
        granularity: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 300,
    ) -> list[Candle]: ...

    @abstractmethod
    async def get_balances(self) -> list[Balance]: ...

    @abstractmethod
    async def fee_rates(self) -> FeeRates: ...

    @abstractmethod
    async def preview_limit(
        self,
        *,
        product_id: str,
        side: OrderSide,
        price: Decimal,
        size: Decimal,
    ) -> dict[str, object]: ...

    @abstractmethod
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
    ) -> Order: ...

    @abstractmethod
    async def place_ioc(
        self,
        *,
        client_order_id: str,
        product_id: str,
        side: OrderSide,
        size: Decimal,
        price: Decimal | None = None,
    ) -> Order: ...

    @abstractmethod
    async def cancel(self, order_id: str) -> None: ...

    @abstractmethod
    async def cancel_many(self, order_ids: list[str]) -> None: ...

    @abstractmethod
    async def get_order(self, order_id: str) -> Order | None: ...

    @abstractmethod
    async def list_open_orders(self, product_id: str | None = None) -> list[Order]: ...

    @abstractmethod
    async def list_fills(
        self,
        *,
        product_id: str | None = None,
        order_id: str | None = None,
    ) -> list[Fill]: ...

    async def subscribe_tickers(
        self,
        product_ids: list[str],
        callback: TickerCallback,
    ) -> None:
        return None

    async def subscribe_user(self, callback: UserCallback) -> None:
        return None

    async def close(self) -> None:
        return None
