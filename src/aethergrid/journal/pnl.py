from __future__ import annotations

from decimal import Decimal

from aethergrid.domain.models import BotRuntime
from aethergrid.persistence.repo import Repository


async def record_pnl(repo: Repository, runtime: BotRuntime, mark: Decimal) -> None:
    equity = runtime.inventory.equity(mark)
    unrealized = equity - runtime.inventory.starting_equity - runtime.inventory.realized_pnl
    await repo.save_pnl(
        bot_id=runtime.bot_id,
        product_id=runtime.product_id,
        mark=mark,
        equity=equity,
        realized=runtime.inventory.realized_pnl,
        unrealized=unrealized,
        fees=runtime.inventory.fees_paid,
        base_inventory=runtime.inventory.base,
        quote_inventory=runtime.inventory.quote,
    )
