from __future__ import annotations

from decimal import Decimal

import pytest

from aethergrid.domain.models import GridConfig, Product, Ticker
from aethergrid.strategy.grid import GridEngine


@pytest.fixture
def product() -> Product:
    return Product(
        product_id="BTC-USD",
        base_currency="BTC",
        quote_currency="USD",
        status="online",
        quote_increment=Decimal("0.01"),
        base_increment=Decimal("0.00000001"),
        base_min_size=Decimal("0.00001"),
        min_market_funds=Decimal("1"),
        price=Decimal("100000"),
    )


@pytest.fixture
def engine(product: Product) -> GridEngine:
    return GridEngine(product, fee_rate=Decimal("0.006"))


@pytest.fixture
def grid_config() -> GridConfig:
    return GridConfig(
        product_id="BTC-USD",
        investment=Decimal("10000"),
        lower_price=Decimal("90000"),
        upper_price=Decimal("110000"),
        grid_levels=11,
        trailing_up=True,
        trailing_down=True,
        take_profit_pct=Decimal("0.10"),
        stop_loss_pct=Decimal("0.15"),
        flatten_on_stop=True,
    )


@pytest.fixture
def ticker() -> Ticker:
    return Ticker(
        product_id="BTC-USD",
        price=Decimal("100000"),
        bid=Decimal("99990"),
        ask=Decimal("100010"),
    )
