from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from aethergrid.ai.operator import AIOperator
from aethergrid.bots.manager import BotManager
from aethergrid.config import Settings
from aethergrid.domain.models import Fill, Order, Product, Ticker
from aethergrid.exchange.base import Exchange
from aethergrid.exchange.paper import PaperExchange
from aethergrid.logging import get_logger, setup_logging
from aethergrid.market.products import PublicMarket, filter_spot
from aethergrid.mode import ModeError, assert_mode_allowed, persist_runtime_mode, record_mode
from aethergrid.persistence.db import get_engine, get_session_factory, init_db
from aethergrid.persistence.repo import Repository
from aethergrid.risk.limits import RiskEngine
from aethergrid.risk.portfolio import snapshot_from
from aethergrid.security import (
    assert_trade_only_key_docs,
    clear_kill_switch,
    kill_switch_tripped,
    trip_kill_switch,
)

log = get_logger("runtime")


class AppRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = get_engine(settings)
        self.sessions = get_session_factory(self.engine)
        self.repo = Repository(self.sessions)
        self.risk = RiskEngine(settings)
        self.market: PublicMarket | None = None
        self.exchange: Exchange
        self.manager: BotManager
        self.operator: AIOperator
        self.products: list[Product] = []
        self.ticks: dict[str, Ticker] = {}
        self.tasks: list[asyncio.Task[None]] = []
        self.started = False
        self.worker_enabled = settings.embed_worker
        self._stop = asyncio.Event()
        self.ai_paused = False
        self._ticks_since_ai = 0

    @classmethod
    async def create(cls, settings: Settings | None = None) -> AppRuntime:
        settings = settings or Settings()
        setup_logging(settings)
        assert_mode_allowed(settings)
        rt = cls(settings)
        await init_db(rt.engine)
        rt._attach_venue()
        return rt

    def _attach_venue(self) -> None:
        settings = self.settings
        if settings.mode == "live":
            from aethergrid.exchange.coinbase import CoinbaseExchange

            assert_trade_only_key_docs()
            self.market = PublicMarket()
            self.exchange = CoinbaseExchange(settings, market=self.market)
        elif settings.mode == "demo":
            from aethergrid.exchange.demo import DemoExchange

            self.market = None
            self.exchange = DemoExchange(
                starting_quote=settings.demo_quote_balance,
                fee_bps=settings.demo_fee_bps,
            )
        else:
            self.market = PublicMarket()
            self.exchange = PaperExchange(
                self.market,
                quote_currency=settings.paper_quote_currency,
                starting_quote=settings.paper_quote_balance,
                fee_bps=settings.paper_fee_bps,
            )
        self.manager = BotManager(
            settings=settings,
            exchange=self.exchange,
            repo=self.repo,
            risk=self.risk,
        )
        self.operator = AIOperator(
            settings=settings,
            manager=self.manager,
            repo=self.repo,
            risk=self.risk,
        )
        self.operator.enabled = settings.ai_enabled

    async def switch_mode(
        self,
        target: str,
        *,
        confirmation: str = "",
        cancel_live_orders: bool = True,
    ) -> dict[str, object]:
        from aethergrid.vercel_env import on_vercel

        if target not in {"demo", "paper", "live"}:
            raise ModeError(f"unknown mode {target}")
        if on_vercel() and target == "live":
            raise ModeError(
                "Vercel hosts the demo UI only. Live Coinbase trading requires Docker or a local process."
            )
        current = self.settings.mode
        if target == current and (target != "live" or self.settings.live_confirmed):
            return {"mode": current, "unchanged": True}

        if target == "live":
            if not self.settings.has_coinbase_keys:
                raise ModeError(
                    "CDP API key name and private key must be set in server env — never in the browser."
                )
            if confirmation.strip() != "I UNDERSTAND THE RISK":
                raise ModeError("Type I UNDERSTAND THE RISK to enable live trading.")

        if current == "live" and target != "live" and cancel_live_orders:
            await self.manager.cancel_all_and_stop(flatten=False)

        old_url = self.settings.effective_database_url
        self.settings.mode = target  # type: ignore[assignment]
        self.settings.live_confirmed = target == "live"
        if target in {"demo", "live"}:
            self.settings.ai_enabled = True
        persist_runtime_mode(
            self.settings,
            mode=target,
            live_confirmed=self.settings.live_confirmed,
            ai_enabled=self.settings.ai_enabled,
        )
        record_mode(self.settings)

        was_started = self.started
        self.started = False
        self._stop.set()
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        try:
            await self.exchange.close()
        except Exception:  # noqa: BLE001
            pass
        if old_url != self.settings.effective_database_url:
            await self.engine.dispose()
            self.engine = get_engine(self.settings)
            await init_db(self.engine)
            self.sessions = get_session_factory(self.engine)
            self.repo = Repository(self.sessions)
        self._stop = asyncio.Event()
        self._attach_venue()
        if was_started:
            await self.start()
        log.info("mode_switched", from_mode=current, to_mode=target, live=self.settings.is_live)
        return {
            "mode": self.settings.mode,
            "live": self.settings.is_live,
            "demo": self.settings.is_demo,
            "has_coinbase_keys": self.settings.has_coinbase_keys,
            "ai_enabled": self.settings.ai_enabled,
            "cancelled_live_orders": bool(current == "live" and target != "live" and cancel_live_orders),
        }

    async def start(self) -> None:
        if self.started:
            return
        try:
            self.products = await self.exchange.list_products()
        except Exception as exc:  # noqa: BLE001
            log.warning("product_discovery_failed", error=str(exc))
            self.products = []
        self.manager.products = {p.product_id: p for p in self.products}
        await self.manager.bootstrap()
        if self.settings.is_demo and self.settings.demo_seed_on_boot:
            from aethergrid.demo.seed import seed_if_empty

            await seed_if_empty(self)
        # Paper/demo fills are forwarded from the ticker loop. Live fills arrive via user WS + reconcile.
        if not isinstance(self.exchange, PaperExchange):
            await self.exchange.subscribe_user(self._on_user_event)
        self.started = True
        if self.worker_enabled:
            self.tasks.append(asyncio.create_task(self._ticker_loop(), name="ticker"))
            self.tasks.append(asyncio.create_task(self._reconcile_loop(), name="reconcile"))
            self.tasks.append(asyncio.create_task(self._ai_loop(), name="ai"))
            self.tasks.append(asyncio.create_task(self._kill_watch(), name="kill"))
            self.tasks.append(asyncio.create_task(self._pnl_loop(), name="pnl"))
        log.info(
            "runtime_started",
            mode=self.settings.mode,
            live=self.settings.is_live,
            products=len(self.products),
            tradable=len(filter_spot(self.products)),
            worker=self.worker_enabled,
        )

    async def stop(self) -> None:
        self._stop.set()
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        await self.exchange.close()
        await self.engine.dispose()
        self.started = False
        log.info("runtime_stopped")

    async def tick_once(self, *, steps: int = 1, run_ai: bool = False) -> dict[str, object]:
        """Advance the demo/paper engine one (or more) steps. Safe on serverless."""
        if not self.started:
            return {"ok": False, "reason": "not_started"}
        if kill_switch_tripped(self.settings):
            return {"ok": False, "reason": "kill_switch"}
        pids = {b.product_id for b in self.manager.list_runtime()}
        if not pids:
            pids = {"BTC-USD", "ETH-USD"}
        fills_n = 0
        for _ in range(max(1, steps)):
            for pid in pids:
                try:
                    ticker = await self.exchange.get_ticker(pid)
                except Exception as exc:  # noqa: BLE001
                    log.warning("ticker_failed", product_id=pid, error=str(exc))
                    continue
                self.ticks[pid] = ticker
                await self.manager.on_ticker(ticker)
                if isinstance(self.exchange, PaperExchange):
                    fills = await self.exchange.on_ticker(ticker)
                    for fill in fills:
                        await self.manager.on_fill(fill)
                        fills_n += 1
            self._ticks_since_ai += 1
        ai_action = None
        should_ai = run_ai or (self.settings.ai_enabled and not self.operator.paused and self._ticks_since_ai >= 8)
        if should_ai:
            self._ticks_since_ai = 0
            try:
                action = await self.operator.step(self.products, self.ticks)
                ai_action = action.action
            except Exception as exc:  # noqa: BLE001
                log.error("tick_ai_failed", error=str(exc))
                ai_action = "error"
        return {
            "ok": True,
            "fills": fills_n,
            "products": sorted(pids),
            "ai": ai_action,
            "mode": self.settings.mode,
            "worker": self.worker_enabled,
        }

    async def _on_user_event(self, event: Order | Fill) -> None:
        if isinstance(event, Fill):
            await self.manager.on_fill(event)

    async def _ticker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if kill_switch_tripped(self.settings):
                    await asyncio.sleep(1)
                    continue
                pids = {b.product_id for b in self.manager.list_runtime()}
                if not pids:
                    pids = {"BTC-USD", "ETH-USD"}
                for pid in pids:
                    try:
                        ticker = await self.exchange.get_ticker(pid)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("ticker_failed", product_id=pid, error=str(exc))
                        continue
                    self.ticks[pid] = ticker
                    await self.manager.on_ticker(ticker)
                    if isinstance(self.exchange, PaperExchange):
                        fills = await self.exchange.on_ticker(ticker)
                        for fill in fills:
                            await self.manager.on_fill(fill)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("ticker_loop_error", error=str(exc))
            await asyncio.sleep(self.settings.ticker_poll_seconds)

    async def _reconcile_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.sleep(self.settings.reconcile_interval_seconds)
                if kill_switch_tripped(self.settings):
                    continue
                await self.manager.reconcile_all()
                for runtime in self.manager.list_runtime():
                    if runtime.status.value != "running":
                        continue
                    try:
                        c1 = await self.exchange.get_candles(runtime.product_id, "ONE_MINUTE", limit=30)
                        c5 = await self.exchange.get_candles(runtime.product_id, "FIVE_MINUTE", limit=30)
                        c1h = await self.exchange.get_candles(runtime.product_id, "ONE_HOUR", limit=40)
                        await self.manager.apply_protection(runtime, c1, c5, c1h)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("protection_failed", bot_id=runtime.bot_id, error=str(exc))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("reconcile_loop_error", error=str(exc))

    async def _ai_loop(self) -> None:
        await asyncio.sleep(5)
        while not self._stop.is_set():
            try:
                if self.settings.ai_enabled and not self.operator.paused:
                    await self.operator.step(self.products, self.ticks)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("ai_loop_error", error=str(exc))
            await asyncio.sleep(self.settings.ai_interval_seconds)

    async def _kill_watch(self) -> None:
        tripped = False
        while not self._stop.is_set():
            try:
                if kill_switch_tripped(self.settings) and not tripped:
                    tripped = True
                    log.warning("kill_switch_engaged")
                    self.operator.paused = True
                    await self.manager.cancel_all_and_stop(flatten=self.settings.flatten_on_kill)
                    await self.repo.save_risk(
                        event_id=f"kill_{int(datetime.now(UTC).timestamp())}",
                        severity="kill",
                        code="KILL_SWITCH",
                        message="Kill switch engaged — cancelled opens, AI stopped",
                    )
                if not kill_switch_tripped(self.settings):
                    tripped = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("kill_watch_error", error=str(exc))
            await asyncio.sleep(1)

    async def _pnl_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.sleep(15)
                for runtime in self.manager.list_runtime():
                    mark = runtime.last_mark
                    if mark:
                        from aethergrid.journal.pnl import record_pnl

                        await record_pnl(self.repo, runtime, mark)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("pnl_loop_error", error=str(exc))

    def overview(self) -> dict[str, object]:
        bots = [
            b
            for b in self.manager.list_runtime()
            if b.status.value not in {"archived", "stopped"}
        ]
        snap = snapshot_from(
            bots,
            [],
            self.ticks,
            quote=self.settings.paper_quote_currency,
            paper_start=self.settings.virtual_quote_balance,
        )
        return {
            "mode": self.settings.mode,
            "live": self.settings.is_live,
            "demo": self.settings.is_demo,
            "kill_switch": kill_switch_tripped(self.settings),
            "ai_enabled": self.settings.ai_enabled and not self.operator.paused,
            "equity": str(snap.equity),
            "realized": str(snap.realized),
            "unrealized": str(snap.unrealized),
            "daily_realized": str(snap.daily_realized),
            "drawdown_pct": str(snap.drawdown_pct),
            "bots": snap.bots,
            "open_orders": snap.open_orders,
            "products": len(self.products),
            "last_ai": self.operator.last_action.model_dump(mode="json") if self.operator.last_action else None,
            "last_ai_at": self.operator.last_at.isoformat() if self.operator.last_at else None,
        }

    async def engage_kill(self, reason: str) -> None:
        trip_kill_switch(self.settings, reason)
        self.operator.paused = True
        await self.manager.cancel_all_and_stop(flatten=self.settings.flatten_on_kill)

    def clear_kill(self) -> None:
        clear_kill_switch(self.settings)
        self.operator.paused = False
