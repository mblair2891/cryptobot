from __future__ import annotations

from aethergrid.domain.models import Fill
from aethergrid.logging import get_logger
from aethergrid.persistence.repo import Repository

log = get_logger("journal")


async def record_fill(repo: Repository, fill: Fill) -> None:
    await repo.save_fill(fill)
    log.info(
        "fill",
        fill_id=fill.fill_id,
        bot_id=fill.bot_id,
        product_id=fill.product_id,
        side=fill.side.value,
        price=str(fill.price),
        size=str(fill.size),
        fee=str(fill.fee),
    )
