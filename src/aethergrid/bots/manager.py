from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aethergrid.config import Settings
from aethergrid.domain.enums import BotStatus, IntentKind, OrderSide, OrderStatus, Venue
from aethergrid.domain.models import BotRuntime, Fill, GridConfig, Order, OrderIntent, Product, Ticker
from aethergrid.exchange.base import Exchange
from aethergrid.exchange.coinbase import reconcile_orders
from aethergrid.journal.pnl import record_pnl
from aethergrid.journal.trades import record_fill
from aethergrid.logging import get_logger
from aethergrid.money import ZERO
from aethergrid.persistence.repo import Repository
from aethergrid.risk.limits import RiskEngine
from aethergrid.strategy.grid import GridEngine
from aethergrid.strategy.protection import apply_protection

log = get_logger("bots")


class BotManager:
    def __init__(
        self,
        *,
        settings: Settings,
        exchange: Exchange,
        repo: Repository,
        risk: RiskEngine,
        products: dict[str, Product] | None = None,
    ) -> None:
        self.settings = settings
        self.exchange = exchange
        self.repo = repo
        self.risk = risk
        self.products: dict[str, Product] = products or {}
        self.runtimes: dict[str, BotRuntime] = {}
        self._engines: dict[str, GridEngine] = {}
        self._fee_rate: Decimal | None = None
        self._lock = asyncio.Lock()
        self.ai_paused = False
        self.last_reconcile: datetime | None = None

    async def bootstrap(self) -> None:
        fee = await self.exchange.fee_rates()
        self._fee_rate = fee.maker
        bots = await self.repo.list_bots(include_archived=False)
        for runtime in bots:
            self.runtimes[runtime.bot_id] = runtime
            product = await self._product(runtime.product_id)
            self._engines[runtime.bot_id] = GridEngine(product, self._fee_rate)
        log.info("manager_bootstrap", bots=len(self.runtimes), venue=self.exchange.venue.value)
        await self.reconcile_all()

    async def _product(self, product_id: str) -> Product:
        if product_id not in self.products:
            self.products[product_id] = await self.exchange.get_product(product_id)
        return self.products[product_id]

    def engine_for(self, runtime: BotRuntime) -> GridEngine:
        if runtime.bot_id not in self._engines:
            product = self.products.get(runtime.product_id)
            if product is None:
                raise RuntimeError(f"unknown product {runtime.product_id}")
            self._engines[runtime.bot_id] = GridEngine(product, self._fee_rate or Decimal("0.006"))
        return self._engines[runtime.bot_id]

    async def create_and_start(
        self,
        config: GridConfig,
        mark: Decimal | None = None,
        bot_id: str | None = None,
    ) -> BotRuntime:
        if self.risk.kill_tripped():
            raise RuntimeError("kill switch active")
        active = [
            b
            for b in self.runtimes.values()
            if b.status in {BotStatus.RUNNING, BotStatus.PAUSED, BotStatus.STARTING, BotStatus.COOLDOWN}
        ]
        if len(active) >= self.settings.max_bots:
            raise RuntimeError(f"MAX_BOTS={self.settings.max_bots} reached")
        product = await self._product(config.product_id)
        if not product.tradable:
            raise RuntimeError(f"{config.product_id} is not tradable")
        ticker = await self.exchange.get_ticker(config.product_id)
        mark = mark or ticker.last or ticker.mid
        if mark <= 0:
            raise RuntimeError("no mark price")
        if mark < config.lower_price or mark > config.upper_price:
            raise RuntimeError(
                f"mark {mark} is outside [{config.lower_price}, {config.upper_price}] — "
                "grids need price inside the range"
            )
        fee = self._fee_rate or (await self.exchange.fee_rates()).maker
        engine = GridEngine(product, fee)
        balances = await self.exchange.get_balances()
        quote = product.quote_currency
        cash = next((b.available for b in balances if b.currency.upper() == quote.upper()), ZERO)
        if cash <= 0:
            # Paper USD alias
            cash = next(
                (b.available for b in balances if b.currency.upper() in {"USD", "USDC"}),
                ZERO,
            )
        if cash < config.investment:
            raise RuntimeError(f"insufficient {quote}: {cash} < {config.investment}")
        runtime = engine.initialize(config, mark, cash, bot_id=bot_id)
        runtime.venue = self.exchange.venue
        runtime.status = BotStatus.STARTING
        self.runtimes[runtime.bot_id] = runtime
        self._engines[runtime.bot_id] = engine
        await self.repo.save_bot(runtime)
        await self._sync_book(runtime, mark)
        runtime.status = BotStatus.RUNNING
        await self.repo.save_bot(runtime)
        log.info("bot_started", bot_id=runtime.bot_id, product_id=config.product_id, mark=str(mark))
        return runtime

    async def pause(self, bot_id: str) -> BotRuntime:
        runtime = self._require(bot_id)
        engine = self.engine_for(runtime)
        engine.pause_entries(runtime)
        mark = runtime.last_mark or ZERO
        await self._sync_book(runtime, mark)
        await self.repo.save_bot(runtime)
        return runtime

    async def resume(self, bot_id: str) -> BotRuntime:
        runtime = self._require(bot_id)
        engine = self.engine_for(runtime)
        engine.resume(runtime)
        mark = runtime.last_mark or ZERO
        await self._sync_book(runtime, mark)
        await self.repo.save_bot(runtime)
        return runtime

    async def stop(self, bot_id: str, *, flatten: bool = False) -> BotRuntime:
        runtime = self._require(bot_id)
        engine = self.engine_for(runtime)
        runtime.status = BotStatus.STOPPING
        mark = runtime.last_mark or ZERO
        intents = engine.flatten_intents(runtime, mark) if flatten else engine.cancel_all_intents(runtime, "stop")
        await self._execute(runtime, intents, mark)
        runtime.status = BotStatus.STOPPED
        runtime.archived_at = datetime.now(UTC)
        runtime.status = BotStatus.ARCHIVED
        await self.repo.save_bot(runtime)
        return runtime

    async def add_funds(self, bot_id: str, quote_amount: Decimal) -> BotRuntime:
        runtime = self._require(bot_id)
        engine = self.engine_for(runtime)
        mark = runtime.last_mark or (await self.exchange.get_ticker(runtime.product_id)).last
        engine.add_funds(runtime, quote_amount, mark)
        await self._sync_book(runtime, mark)
        await self.repo.save_bot(runtime)
        return runtime

    async def reconfigure(
        self,
        bot_id: str,
        *,
        lower: Decimal | None = None,
        upper: Decimal | None = None,
        levels: int | None = None,
        trailing_up: bool | None = None,
        trailing_down: bool | None = None,
        take_profit_pct: Decimal | None = None,
        stop_loss_pct: Decimal | None = None,
    ) -> BotRuntime:
        runtime = self._require(bot_id)
        engine = self.engine_for(runtime)
        mark = runtime.last_mark or (await self.exchange.get_ticker(runtime.product_id)).last
        # Cancel existing, rebuild ladder, replace.
        await self._execute(runtime, engine.cancel_all_intents(runtime, "reconfigure"), mark)
        if trailing_up is not None:
            runtime.config.trailing_up = trailing_up
        if trailing_down is not None:
            runtime.config.trailing_down = trailing_down
        if take_profit_pct is not None:
            runtime.config.take_profit_pct = take_profit_pct
        if stop_loss_pct is not None:
            runtime.config.stop_loss_pct = stop_loss_pct
        if any(v is not None for v in (lower, upper, levels)):
            engine.reconfigure_range(runtime, mark, lower=lower, upper=upper, levels=levels)
        await self._sync_book(runtime, mark)
        await self.repo.save_bot(runtime)
        return runtime

    async def trail(self, bot_id: str, direction: str) -> BotRuntime:
        runtime = self._require(bot_id)
        engine = self.engine_for(runtime)
        mark = runtime.last_mark or (await self.exchange.get_ticker(runtime.product_id)).last
        if direction == "up":
            runtime.config.trailing_up = True
        else:
            runtime.config.trailing_down = True
        await self._execute(runtime, engine.cancel_all_intents(runtime, f"trail_{direction}"), mark)
        engine.maybe_trail(runtime, mark)
        await self._sync_book(runtime, mark)
        await self.repo.save_bot(runtime)
        return runtime

    async def on_ticker(self, ticker: Ticker) -> None:
        async with self._lock:
            for runtime in list(self.runtimes.values()):
                if runtime.product_id != ticker.product_id:
                    continue
                if runtime.status not in {BotStatus.RUNNING, BotStatus.PAUSED, BotStatus.COOLDOWN}:
                    continue
                await self._tick_one(runtime, ticker)

    async def on_fill(self, fill: Fill) -> None:
        async with self._lock:
            runtime = self._runtime_for_fill(fill)
            if runtime is None:
                return
            fill.bot_id = runtime.bot_id
            engine = self.engine_for(runtime)
            engine.on_fill(runtime, fill)
            await record_fill(self.repo, fill)
            mark = runtime.last_mark or fill.price
            await self._after_event(runtime, mark)
            await self.repo.save_bot(runtime)

    async def _tick_one(self, runtime: BotRuntime, ticker: Ticker) -> None:
        mark = ticker.last or ticker.mid
        runtime.last_mark = mark
        if runtime.cooldown_until and datetime.now(UTC) < runtime.cooldown_until:
            return
        if runtime.cooldown_until and datetime.now(UTC) >= runtime.cooldown_until:
            runtime.status = BotStatus.RUNNING
            runtime.cooldown_until = None
        engine = self.engine_for(runtime)
        exit_intent = engine.check_exits(runtime, mark)
        if exit_intent:
            flatten = exit_intent.kind == IntentKind.FLATTEN or (
                exit_intent.reason == "stop_loss" and runtime.config.flatten_on_stop
            )
            log.warning("bot_exit", bot_id=runtime.bot_id, reason=exit_intent.reason)
            await self.stop(runtime.bot_id, flatten=flatten)
            return
        trip = self.risk.check_bot(runtime, mark)
        if trip:
            log.warning("bot_risk", bot_id=runtime.bot_id, code=trip.code, message=trip.message)
            await self.repo.save_risk(
                event_id=f"risk_{runtime.bot_id}_{int(datetime.now(UTC).timestamp())}",
                severity=trip.severity.value,
                code=trip.code,
                message=trip.message,
                bot_id=runtime.bot_id,
            )
            if trip.severity.value in {"kill", "critical"}:
                await self.pause(runtime.bot_id)
                return
        shifted = engine.maybe_trail(runtime, mark)
        if shifted:
            await self._sync_book(runtime, mark)
            await self.repo.save_snapshot(runtime, mark)
            await self.repo.save_bot(runtime)
            return
        await self._sync_book(runtime, mark)

    async def _after_event(self, runtime: BotRuntime, mark: Decimal) -> None:
        engine = self.engine_for(runtime)
        exit_intent = engine.check_exits(runtime, mark)
        if exit_intent:
            flatten = exit_intent.kind == IntentKind.FLATTEN or runtime.config.flatten_on_stop
            await self.stop(runtime.bot_id, flatten=flatten)
            return
        await self._sync_book(runtime, mark)
        await record_pnl(self.repo, runtime, mark)

    async def _sync_book(self, runtime: BotRuntime, mark: Decimal) -> None:
        engine = self.engine_for(runtime)
        intents = engine.desired_intents(runtime, mark)
        await self._execute(runtime, intents, mark)

    async def _execute(self, runtime: BotRuntime, intents: list[OrderIntent], mark: Decimal) -> None:
        for intent in intents:
            reason = self.risk.allow_intent(runtime, intent, mark)
            if reason:
                log.warning("intent_blocked", bot_id=runtime.bot_id, reason=reason, kind=intent.kind)
                continue
            try:
                await self._execute_one(runtime, intent)
            except Exception as exc:  # noqa: BLE001
                runtime.last_error = str(exc)
                runtime.error_count += 1
                runtime.status = BotStatus.COOLDOWN
                runtime.cooldown_until = datetime.now(UTC) + timedelta(
                    seconds=runtime.config.cooldown_seconds
                )
                log.error("intent_failed", bot_id=runtime.bot_id, error=str(exc), kind=intent.kind)
                await self.repo.save_bot(runtime)

    async def _execute_one(self, runtime: BotRuntime, intent: OrderIntent) -> None:
        engine = self.engine_for(runtime)
        if intent.kind == IntentKind.CANCEL and intent.cancel_client_order_id:
            order = runtime.open_orders.get(intent.cancel_client_order_id)
            oid = (order.exchange_order_id if order else None) or intent.cancel_client_order_id
            await self.exchange.cancel(oid)
            engine.on_cancel_ack(runtime, intent.cancel_client_order_id)
            if order:
                order.status = OrderStatus.CANCELLED
                await self.repo.save_order(order)
            return
        if intent.kind == IntentKind.REPLACE and intent.cancel_client_order_id:
            order = runtime.open_orders.get(intent.cancel_client_order_id)
            oid = (order.exchange_order_id if order else None) or intent.cancel_client_order_id
            await self.exchange.cancel(oid)
            engine.on_cancel_ack(runtime, intent.cancel_client_order_id)
            intent = OrderIntent(
                kind=IntentKind.PLACE,
                client_order_id=intent.client_order_id,
                side=intent.side,
                price=intent.price,
                size=intent.size,
                tif=intent.tif,
                slot_index=intent.slot_index,
                reason=intent.reason,
            )
        if intent.kind in {IntentKind.PLACE, IntentKind.FLATTEN}:
            if intent.side is None or intent.size is None:
                return
            if intent.kind == IntentKind.FLATTEN:
                order = await self.exchange.place_ioc(
                    client_order_id=intent.client_order_id or f"flat_{runtime.bot_id}",
                    product_id=runtime.product_id,
                    side=intent.side,
                    size=intent.size,
                    price=intent.price,
                )
            else:
                if intent.price is None:
                    return
                order = await self.exchange.place_limit(
                    client_order_id=intent.client_order_id or "",
                    product_id=runtime.product_id,
                    side=intent.side,
                    price=intent.price,
                    size=intent.size,
                    tif=intent.tif,
                    post_only=runtime.config.post_only,
                )
            order.bot_id = runtime.bot_id
            order.slot_index = intent.slot_index
            engine.on_order_ack(runtime, order)
            await self.repo.save_order(order)

    async def apply_protection(
        self,
        runtime: BotRuntime,
        candles_1m: list,
        candles_5m: list,
        candles_1h: list,
    ) -> None:
        mark = runtime.last_mark or ZERO
        if mark <= 0:
            return
        _, halt, reason = apply_protection(
            runtime,
            mark=mark,
            candles_1m=candles_1m,
            candles_5m=candles_5m,
            candles_1h=candles_1h,
        )
        if halt and runtime.status == BotStatus.RUNNING:
            log.warning("protection_halt", bot_id=runtime.bot_id, reason=reason)
            engine = self.engine_for(runtime)
            if runtime.config.breakout_flatten and runtime.protection.breakout_active:
                await self.stop(runtime.bot_id, flatten=True)
                return
            runtime.protection.entries_paused = True
            await self._sync_book(runtime, mark)
            await self.repo.save_bot(runtime)

    async def reconcile_all(self) -> dict[str, object]:
        reports: dict[str, object] = {}
        for runtime in list(self.runtimes.values()):
            if runtime.status not in {BotStatus.RUNNING, BotStatus.PAUSED, BotStatus.COOLDOWN}:
                continue
            report = await reconcile_orders(self.exchange, runtime.open_orders)
            reports[runtime.bot_id] = report
            for cid in report["filled_elsewhere"]:
                fills = await self.exchange.list_fills(order_id=cid)
                for fill in fills:
                    fill.bot_id = runtime.bot_id
                    await self.on_fill(fill)
            for cid in report["missing_on_venue"]:
                order = runtime.open_orders.get(cid)
                if order:
                    # Drop ghost local order; engine will replace if still desired.
                    self.engine_for(runtime).on_cancel_ack(runtime, cid)
            for cid in report["unknown_on_venue"]:
                if self.exchange.venue == Venue.COINBASE:
                    await self.exchange.cancel(cid)
            mark = runtime.last_mark
            if mark:
                await self._sync_book(runtime, mark)
                await self.repo.save_bot(runtime)
        self.last_reconcile = datetime.now(UTC)
        log.info("reconcile_done", bots=len(reports))
        return reports

    async def cancel_all_and_stop(self, *, flatten: bool = False) -> None:
        for runtime in list(self.runtimes.values()):
            if runtime.status in {BotStatus.ARCHIVED, BotStatus.STOPPED}:
                continue
            try:
                await self.stop(runtime.bot_id, flatten=flatten)
            except Exception as exc:  # noqa: BLE001
                log.error("kill_stop_failed", bot_id=runtime.bot_id, error=str(exc))

    def _require(self, bot_id: str) -> BotRuntime:
        runtime = self.runtimes.get(bot_id)
        if not runtime:
            raise KeyError(bot_id)
        return runtime

    def _runtime_for_fill(self, fill: Fill) -> BotRuntime | None:
        if fill.bot_id and fill.bot_id in self.runtimes:
            return self.runtimes[fill.bot_id]
        for runtime in self.runtimes.values():
            if fill.client_order_id in runtime.open_orders:
                return runtime
            if any(o.exchange_order_id == fill.exchange_order_id for o in runtime.open_orders.values()):
                return runtime
        return None

    def list_runtime(self) -> list[BotRuntime]:
        return list(self.runtimes.values())
