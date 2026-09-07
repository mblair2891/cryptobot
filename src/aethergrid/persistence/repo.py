from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethergrid.domain.enums import BotStatus
from aethergrid.domain.models import BotRuntime, Fill, Order, utcnow
from aethergrid.persistence.models import (
    AIDecisionRow,
    BotRow,
    FillRow,
    GridSnapshotRow,
    OrderRow,
    PnLTickRow,
    RiskEventRow,
)


class Repository:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory

    async def save_bot(self, runtime: BotRuntime) -> None:
        payload = runtime.model_dump(mode="json")
        async with self.factory() as session:
            row = await session.get(BotRow, runtime.bot_id)
            if row is None:
                row = BotRow(
                    id=runtime.bot_id,
                    product_id=runtime.product_id,
                    status=runtime.status.value,
                    venue=runtime.venue.value,
                    name=runtime.config.name or runtime.product_id,
                    config_json=payload["config"],
                    state_json=payload,
                    last_error=runtime.last_error,
                    created_at=runtime.created_at,
                    updated_at=utcnow(),
                    archived_at=runtime.archived_at,
                )
                session.add(row)
            else:
                row.product_id = runtime.product_id
                row.status = runtime.status.value
                row.venue = runtime.venue.value
                row.name = runtime.config.name or runtime.product_id
                row.config_json = payload["config"]
                row.state_json = payload
                row.last_error = runtime.last_error
                row.updated_at = utcnow()
                row.archived_at = runtime.archived_at
            await session.commit()

    async def load_bot(self, bot_id: str) -> BotRuntime | None:
        async with self.factory() as session:
            row = await session.get(BotRow, bot_id)
            if row is None:
                return None
            return BotRuntime.model_validate(row.state_json)

    async def list_bots(self, *, include_archived: bool = False) -> list[BotRuntime]:
        async with self.factory() as session:
            stmt = select(BotRow).order_by(desc(BotRow.updated_at))
            if not include_archived:
                stmt = stmt.where(BotRow.status != BotStatus.ARCHIVED.value)
            rows = (await session.execute(stmt)).scalars().all()
            return [BotRuntime.model_validate(r.state_json) for r in rows]

    async def active_bots(self) -> list[BotRuntime]:
        async with self.factory() as session:
            stmt = select(BotRow).where(
                BotRow.status.in_(
                    [
                        BotStatus.RUNNING.value,
                        BotStatus.PAUSED.value,
                        BotStatus.STARTING.value,
                        BotStatus.COOLDOWN.value,
                    ]
                )
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [BotRuntime.model_validate(r.state_json) for r in rows]

    async def save_order(self, order: Order) -> None:
        async with self.factory() as session:
            row = await session.get(OrderRow, order.client_order_id)
            data = dict(
                exchange_order_id=order.exchange_order_id,
                bot_id=order.bot_id or "",
                product_id=order.product_id,
                side=order.side.value,
                price=order.price,
                size=order.size,
                filled_size=order.filled_size,
                fee=order.fee,
                status=order.status.value,
                tif=order.tif.value,
                slot_index=order.slot_index,
                venue=order.venue.value,
                error=order.error,
                updated_at=order.updated_at,
            )
            if row is None:
                session.add(OrderRow(client_order_id=order.client_order_id, created_at=order.created_at, **data))
            else:
                for k, v in data.items():
                    setattr(row, k, v)
            await session.commit()

    async def save_fill(self, fill: Fill) -> None:
        async with self.factory() as session:
            existing = await session.get(FillRow, fill.fill_id)
            if existing:
                await session.commit()
                return
            session.add(
                FillRow(
                    fill_id=fill.fill_id,
                    client_order_id=fill.client_order_id,
                    exchange_order_id=fill.exchange_order_id,
                    bot_id=fill.bot_id,
                    product_id=fill.product_id,
                    side=fill.side.value,
                    price=fill.price,
                    size=fill.size,
                    fee=fill.fee,
                    venue=fill.venue.value,
                    liquidity=fill.liquidity,
                    slot_index=fill.slot_index,
                    ts=fill.ts,
                )
            )
            await session.commit()

    async def recent_fills(self, limit: int = 200, bot_id: str | None = None) -> list[FillRow]:
        async with self.factory() as session:
            stmt = select(FillRow).order_by(desc(FillRow.ts)).limit(limit)
            if bot_id:
                stmt = stmt.where(FillRow.bot_id == bot_id)
            return list((await session.execute(stmt)).scalars().all())

    async def save_pnl(
        self,
        *,
        bot_id: str,
        product_id: str,
        mark: Decimal,
        equity: Decimal,
        realized: Decimal,
        unrealized: Decimal,
        fees: Decimal,
        base_inventory: Decimal,
        quote_inventory: Decimal,
    ) -> None:
        async with self.factory() as session:
            session.add(
                PnLTickRow(
                    bot_id=bot_id,
                    product_id=product_id,
                    mark=mark,
                    equity=equity,
                    realized=realized,
                    unrealized=unrealized,
                    fees=fees,
                    base_inventory=base_inventory,
                    quote_inventory=quote_inventory,
                    ts=datetime.now(UTC),
                )
            )
            await session.commit()

    async def recent_pnl(self, limit: int = 300, bot_id: str | None = None) -> list[PnLTickRow]:
        async with self.factory() as session:
            stmt = select(PnLTickRow).order_by(desc(PnLTickRow.ts)).limit(limit)
            if bot_id:
                stmt = stmt.where(PnLTickRow.bot_id == bot_id)
            return list((await session.execute(stmt)).scalars().all())

    async def save_snapshot(self, runtime: BotRuntime, mark: Decimal) -> None:
        async with self.factory() as session:
            session.add(
                GridSnapshotRow(
                    bot_id=runtime.bot_id,
                    mark=mark,
                    state_json=runtime.model_dump(mode="json"),
                    ts=datetime.now(UTC),
                )
            )
            await session.commit()

    async def save_ai_decision(
        self,
        *,
        decision_id: str,
        action: str,
        executed: bool,
        reason: str,
        features: dict[str, Any],
        action_payload: dict[str, Any],
        error: str | None = None,
    ) -> None:
        async with self.factory() as session:
            session.add(
                AIDecisionRow(
                    id=decision_id,
                    action=action,
                    executed=1 if executed else 0,
                    reason=reason,
                    features_json=features,
                    action_json=action_payload,
                    error=error,
                    ts=datetime.now(UTC),
                )
            )
            await session.commit()

    async def recent_ai(self, limit: int = 100) -> list[AIDecisionRow]:
        async with self.factory() as session:
            stmt = select(AIDecisionRow).order_by(desc(AIDecisionRow.ts)).limit(limit)
            return list((await session.execute(stmt)).scalars().all())

    async def save_risk(
        self,
        *,
        event_id: str,
        severity: str,
        code: str,
        message: str,
        bot_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        async with self.factory() as session:
            session.add(
                RiskEventRow(
                    id=event_id,
                    severity=severity,
                    code=code,
                    message=message,
                    bot_id=bot_id,
                    details_json=details or {},
                    ts=datetime.now(UTC),
                )
            )
            await session.commit()

    async def recent_risk(self, limit: int = 100) -> list[RiskEventRow]:
        async with self.factory() as session:
            stmt = select(RiskEventRow).order_by(desc(RiskEventRow.ts)).limit(limit)
            return list((await session.execute(stmt)).scalars().all())

    async def daily_realized(self) -> Decimal:
        from datetime import date

        from sqlalchemy import func

        today = date.today().isoformat()
        async with self.factory() as session:
            stmt = select(func.coalesce(func.sum(PnLTickRow.realized), 0)).where(
                PnLTickRow.ts >= datetime.fromisoformat(today).replace(tzinfo=UTC)
            )
            # Sum of latest realized per bot is better; use fills as approximation if ticks sparse.
            value = (await session.execute(stmt)).scalar_one()
            return Decimal(str(value or 0))
