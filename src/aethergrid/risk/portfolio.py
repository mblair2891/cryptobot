from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from aethergrid.domain.models import Balance, BotRuntime, Ticker
from aethergrid.money import ZERO


class PortfolioSnapshot(BaseModel):
    equity: Decimal = ZERO
    cash: Decimal = ZERO
    starting_equity: Decimal = ZERO
    realized: Decimal = ZERO
    unrealized: Decimal = ZERO
    live_notional: Decimal = ZERO
    open_orders: int = 0
    bots: int = 0
    daily_realized: Decimal = ZERO
    balances: list[Balance] = Field(default_factory=list)

    @property
    def drawdown_pct(self) -> Decimal:
        if self.starting_equity <= 0:
            return ZERO
        dd = (self.starting_equity - self.equity) / self.starting_equity
        return dd if dd > 0 else ZERO


def snapshot_from(
    bots: list[BotRuntime],
    balances: list[Balance],
    ticks: dict[str, Ticker],
    quote: str = "USD",
    paper_start: Decimal = ZERO,
) -> PortfolioSnapshot:
    cash = ZERO
    for b in balances:
        if b.currency.upper() in {quote.upper(), "USD", "USDC", "USDT"}:
            cash += b.available
    realized = ZERO
    unrealized = ZERO
    equity = cash
    open_orders = 0
    live_notional = ZERO
    starting = ZERO
    daily = ZERO
    counted_cash = cash
    for bot in bots:
        mark = bot.last_mark or (ticks[bot.product_id].last if bot.product_id in ticks else ZERO)
        realized += bot.inventory.realized_pnl
        daily += bot.daily_realized
        starting += bot.inventory.starting_equity
        eq = bot.inventory.equity(mark) if mark else bot.inventory.quote
        unrealized += eq - bot.inventory.starting_equity - bot.inventory.realized_pnl
        open_orders += sum(1 for o in bot.open_orders.values() if o.is_open)
        live_notional += bot.config.investment
        # Equity: paper inventory is virtual; live uses exchange balances.
        if bot.venue.value in {"paper", "demo"}:
            counted_cash = ZERO  # replaced below
    if any(b.venue.value in {"paper", "demo"} for b in bots) or not bots:
        equity = paper_start
        for bot in bots:
            mark = bot.last_mark or ZERO
            if mark:
                equity = ZERO
                break
        if bots:
            equity = ZERO
            seen = set()
            for bot in bots:
                mark = bot.last_mark or (ticks[bot.product_id].last if bot.product_id in ticks else ZERO)
                equity += bot.inventory.equity(mark) if mark else bot.inventory.quote
                seen.add(bot.bot_id)
            leftover = paper_start - sum((b.inventory.starting_equity for b in bots), ZERO)
            if leftover > 0:
                equity += leftover
        else:
            equity = cash or paper_start
    else:
        # Live: mark-to-market using balances + leftover quote.
        equity = cash
        for bot in bots:
            mark = bot.last_mark or ZERO
            if mark:
                equity += bot.inventory.base * mark
        _ = counted_cash
    if starting <= 0:
        starting = paper_start or equity
    return PortfolioSnapshot(
        equity=equity,
        cash=cash,
        starting_equity=starting,
        realized=realized,
        unrealized=unrealized,
        live_notional=live_notional,
        open_orders=open_orders,
        bots=len(bots),
        daily_realized=daily,
        balances=balances,
    )
