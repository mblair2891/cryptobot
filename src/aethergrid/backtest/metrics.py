from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from aethergrid.money import ZERO


class BacktestReport(BaseModel):
    product_id: str
    days: int
    bars: int
    trades: int = 0
    grid_cycles: int = 0
    realized_pnl: Decimal = ZERO
    fees: Decimal = ZERO
    max_drawdown_pct: Decimal = ZERO
    ending_equity: Decimal = ZERO
    starting_equity: Decimal = ZERO
    time_in_market_pct: Decimal = ZERO
    caveat: str = (
        "Candle backtests overstate grid performance: they assume fills at "
        "touch + fee + half-spread and cannot model intra-bar noise, queue position, or gaps."
    )
    notes: list[str] = Field(default_factory=list)
