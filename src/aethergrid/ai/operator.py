from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from aethergrid.ai.features import OperatorFeatures, bot_features, pair_features
from aethergrid.ai.llm import advise, merge_llm_rank
from aethergrid.ai.policies import (
    Action,
    AddFunds,
    FlattenAndArchive,
    Noop,
    PauseEntries,
    ProposeNewBot,
    ReconfigureBot,
    ResumeBot,
    SelectUniverse,
    StopLossClose,
    TakeProfitClose,
    TrailDown,
    TrailUp,
    action_to_dict,
    decide,
    validate_action,
)
from aethergrid.bots.manager import BotManager
from aethergrid.config import Settings
from aethergrid.domain.models import GridConfig, Product, Ticker, new_id
from aethergrid.logging import get_logger
from aethergrid.market.products import filter_spot
from aethergrid.money import ZERO
from aethergrid.persistence.repo import Repository
from aethergrid.risk.limits import RiskEngine

log = get_logger("ai.operator")

WATCHLIST_FALLBACK = (
    "BTC-USD",
    "ETH-USD",
    "SOL-USD",
    "BTC-USDC",
    "ETH-USDC",
    "LINK-USD",
    "XRP-USD",
    "ADA-USD",
    "DOGE-USD",
    "AVAX-USD",
    "LTC-USD",
    "BCH-USD",
)


class AIOperator:
    """Closed-loop operator. May only emit the Action schema and run it through BotManager + risk."""

    def __init__(
        self,
        *,
        settings: Settings,
        manager: BotManager,
        repo: Repository,
        risk: RiskEngine,
    ) -> None:
        self.settings = settings
        self.manager = manager
        self.repo = repo
        self.risk = risk
        self.enabled = settings.ai_enabled
        self.last_action: Action = Noop(reason="boot")
        self.last_features: OperatorFeatures | None = None
        self.last_error: str | None = None
        self.last_at: datetime | None = None
        self.paused = False
        self.last_universe: list[str] = []

    async def ingest(self, products: list[Product], ticks: dict[str, Ticker]) -> OperatorFeatures:
        fee = self.manager._fee_rate or Decimal("0.006")
        usd_spot = filter_spot(products, quotes=("USD", "USDC"))
        usd_spot.sort(key=lambda p: p.volume_24h, reverse=True)
        # Always consider a core watchlist plus top liquidity names discovered at runtime.
        wanted = {p.product_id for p in usd_spot[:40]}
        for pid in WATCHLIST_FALLBACK:
            wanted.add(pid)
        by_id = {p.product_id: p for p in products}
        pairs = []
        errors: list[str] = []
        for pid in list(wanted)[:30]:
            product = by_id.get(pid)
            if product is None:
                continue
            ticker = ticks.get(pid)
            if ticker is None:
                try:
                    ticker = await self.manager.exchange.get_ticker(pid)
                    ticks[pid] = ticker
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{pid}: {exc}")
                    continue
            try:
                c1h = await self.manager.exchange.get_candles(pid, "ONE_HOUR", limit=180)
                c1d = await self.manager.exchange.get_candles(pid, "ONE_DAY", limit=45)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{pid} candles: {exc}")
                c1h, c1d = [], []
            pairs.append(pair_features(product, ticker, c1h, c1d, fee))
        bots = [bot_features(r) for r in self.manager.list_runtime()]
        cash = ZERO
        try:
            balances = await self.manager.exchange.get_balances()
            cash = sum(
                (b.available for b in balances if b.currency.upper() in {"USD", "USDC", "USDT"}),
                ZERO,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"balances: {exc}")
        equity = sum((b.equity for b in bots), cash if not bots else ZERO)
        daily = sum((r.daily_realized for r in self.manager.list_runtime()), ZERO)
        features = OperatorFeatures(
            pairs=sorted(pairs, key=lambda p: p.score, reverse=True),
            bots=bots,
            equity=equity,
            cash=cash,
            daily_realized=daily,
            kill_switch=self.risk.kill_tripped(),
            mode=self.settings.mode,
            errors=errors,
        )
        self.last_features = features
        return features

    async def step(self, products: list[Product], ticks: dict[str, Ticker]) -> Action:
        if self.settings.mode == "live" and not self.settings.live_confirmed:
            action: Action = Noop(reason="live AI requires I UNDERSTAND THE RISK")
            self.last_action = action
            return action
        if not self.enabled or self.paused:
            action = Noop(reason="ai paused")
            self.last_action = action
            return action
        if self.risk.kill_tripped():
            action = Noop(reason="kill switch")
            await self._journal(action, OperatorFeatures(kill_switch=True), executed=False, error="kill")
            return action
        features = await self.ingest(products, ticks)
        if self.settings.is_demo:
            advice = None
        else:
            advice = await advise(self.settings, features)
        features = merge_llm_rank(features, advice)
        budget = min(self.settings.max_live_notional, features.cash * Decimal("0.2") or Decimal("100"))
        if self.settings.mode == "paper":
            budget = min(Decimal("5000"), features.cash * Decimal("0.15") or Decimal("1000"))
        elif self.settings.mode == "demo":
            budget = min(Decimal("1500"), features.cash * Decimal("0.15") or Decimal("500"))
        action = decide(features, max_bots=self.settings.max_bots, quote_budget=budget)
        rejection = validate_action(action, features, self.settings.max_bots)
        executed = False
        error = rejection
        if rejection is None:
            try:
                await self._execute(action)
                executed = not isinstance(action, Noop)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                log.error("ai_execute_failed", error=error, action=action.action)
        else:
            log.info("ai_rejected", reason=rejection, action=action.action)
            action = Noop(reason=f"validator: {rejection}")
        await self._journal(action, features, executed=executed, error=error)
        self.last_action = action
        self.last_error = error
        self.last_at = datetime.now(UTC)
        return action

    async def _execute(self, action: Action) -> None:
        if isinstance(action, Noop):
            return
        if isinstance(action, SelectUniverse):
            self.last_universe = list(action.ranked)
            return
        if isinstance(action, ProposeNewBot):
            cfg = GridConfig(
                product_id=action.product_id,
                investment=action.investment_quote,
                lower_price=action.lower_price,
                upper_price=action.upper_price,
                grid_levels=action.grid_levels,
                trailing_up=action.trailing_up,
                trailing_down=action.trailing_down,
                take_profit_pct=action.take_profit_pct,
                stop_loss_pct=action.stop_loss_pct,
                name=f"ai-{action.product_id}",
            )
            await self.manager.create_and_start(cfg)
            return
        if isinstance(action, ReconfigureBot):
            await self.manager.reconfigure(
                action.bot_id,
                lower=action.lower_price,
                upper=action.upper_price,
                levels=action.grid_levels,
                trailing_up=action.trailing_up,
                trailing_down=action.trailing_down,
                take_profit_pct=action.take_profit_pct,
                stop_loss_pct=action.stop_loss_pct,
            )
            return
        if isinstance(action, TrailUp):
            await self.manager.trail(action.bot_id, "up")
            return
        if isinstance(action, TrailDown):
            await self.manager.trail(action.bot_id, "down")
            return
        if isinstance(action, PauseEntries):
            await self.manager.pause(action.bot_id)
            return
        if isinstance(action, ResumeBot):
            await self.manager.resume(action.bot_id)
            return
        if isinstance(action, AddFunds):
            await self.manager.add_funds(action.bot_id, action.quote_amount)
            return
        if isinstance(action, TakeProfitClose):
            await self.manager.stop(action.bot_id, flatten=False)
            return
        if isinstance(action, StopLossClose):
            await self.manager.stop(action.bot_id, flatten=True)
            return
        if isinstance(action, FlattenAndArchive):
            await self.manager.stop(action.bot_id, flatten=True)
            return
        raise RuntimeError(f"unhandled action {action}")

    async def _journal(
        self,
        action: Action,
        features: OperatorFeatures,
        *,
        executed: bool,
        error: str | None,
    ) -> None:
        reason = getattr(action, "reason", "")
        await self.repo.save_ai_decision(
            decision_id=new_id("ai"),
            action=action.action,
            executed=executed,
            reason=reason,
            features=features.model_dump(mode="json"),
            action_payload=action_to_dict(action),
            error=error,
        )
        log.info(
            "ai_decision",
            action=action.action,
            executed=executed,
            reason=reason,
            error=error,
        )
