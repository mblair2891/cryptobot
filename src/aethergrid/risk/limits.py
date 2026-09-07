from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from aethergrid.config import Settings
from aethergrid.domain.enums import RiskSeverity
from aethergrid.domain.models import BotRuntime, OrderIntent
from aethergrid.money import ZERO
from aethergrid.security import kill_switch_tripped


@dataclass
class HardLimits:
    max_live_notional: Decimal
    max_bots: int
    max_open_orders_total: int
    max_open_orders_per_bot: int
    max_daily_realized_loss: Decimal
    max_drawdown_pct: Decimal
    max_base_inventory_per_bot: Decimal
    min_cash_reserve: Decimal
    flatten_on_kill: bool

    @classmethod
    def from_settings(cls, settings: Settings) -> HardLimits:
        return cls(
            max_live_notional=settings.max_live_notional,
            max_bots=settings.max_bots,
            max_open_orders_total=settings.max_open_orders_total,
            max_open_orders_per_bot=settings.max_open_orders_per_bot,
            max_daily_realized_loss=settings.max_daily_realized_loss,
            max_drawdown_pct=settings.max_drawdown_pct,
            max_base_inventory_per_bot=settings.max_base_inventory_per_bot,
            min_cash_reserve=settings.min_cash_reserve,
            flatten_on_kill=settings.flatten_on_kill,
        )


@dataclass
class RiskTrip:
    code: str
    message: str
    severity: RiskSeverity
    flatten: bool = False
    stop_ai: bool = False
    cancel_opens: bool = True


class RiskEngine:
    """Hard kill-switches. The AI operator cannot disable these."""

    def __init__(self, settings: Settings, limits: HardLimits | None = None) -> None:
        self.settings = settings
        self.limits = limits or HardLimits.from_settings(settings)

    def kill_tripped(self) -> bool:
        return kill_switch_tripped(self.settings)

    def check_portfolio(
        self,
        *,
        bots: list[BotRuntime],
        equity: Decimal,
        starting_equity: Decimal,
        daily_realized: Decimal,
        cash: Decimal,
        live_notional: Decimal,
        open_orders: int,
    ) -> RiskTrip | None:
        if self.kill_tripped():
            return RiskTrip(
                code="KILL_SWITCH",
                message="Kill switch file/API is active",
                severity=RiskSeverity.KILL,
                flatten=self.limits.flatten_on_kill,
                stop_ai=True,
            )
        active = [b for b in bots if b.status.value in {"running", "paused", "starting", "cooldown"}]
        if len(active) > self.limits.max_bots:
            return RiskTrip(
                code="MAX_BOTS",
                message=f"{len(active)} bots exceeds MAX_BOTS={self.limits.max_bots}",
                severity=RiskSeverity.KILL,
                stop_ai=True,
            )
        if open_orders > self.limits.max_open_orders_total:
            return RiskTrip(
                code="MAX_OPEN_ORDERS",
                message=f"{open_orders} open orders exceeds cap {self.limits.max_open_orders_total}",
                severity=RiskSeverity.CRITICAL,
                stop_ai=True,
            )
        if self.settings.is_live and live_notional > self.limits.max_live_notional:
            return RiskTrip(
                code="MAX_LIVE_NOTIONAL",
                message=f"live notional {live_notional} > {self.limits.max_live_notional}",
                severity=RiskSeverity.KILL,
                stop_ai=True,
            )
        if daily_realized < ZERO and abs(daily_realized) >= self.limits.max_daily_realized_loss:
            return RiskTrip(
                code="MAX_DAILY_REALIZED_LOSS",
                message=f"daily realized {daily_realized} breached {self.limits.max_daily_realized_loss}",
                severity=RiskSeverity.KILL,
                stop_ai=True,
            )
        if starting_equity > 0:
            dd = (starting_equity - equity) / starting_equity
            if dd >= self.limits.max_drawdown_pct:
                return RiskTrip(
                    code="MAX_DRAWDOWN",
                    message=f"drawdown {float(dd):.2%} >= {float(self.limits.max_drawdown_pct):.2%}",
                    severity=RiskSeverity.KILL,
                    flatten=self.limits.flatten_on_kill,
                    stop_ai=True,
                )
        if cash < self.limits.min_cash_reserve:
            return RiskTrip(
                code="MIN_CASH_RESERVE",
                message=f"cash {cash} < reserve {self.limits.min_cash_reserve}",
                severity=RiskSeverity.CRITICAL,
                stop_ai=True,
                cancel_opens=False,
            )
        return None

    def check_bot(self, runtime: BotRuntime, mark: Decimal) -> RiskTrip | None:
        cap = self.limits.max_base_inventory_per_bot
        if cap > 0 and runtime.inventory.base > cap:
            return RiskTrip(
                code="MAX_BASE_INVENTORY",
                message=f"bot {runtime.bot_id} base {runtime.inventory.base} > {cap}",
                severity=RiskSeverity.CRITICAL,
                flatten=False,
                stop_ai=False,
            )
        opens = sum(1 for o in runtime.open_orders.values() if o.is_open)
        if opens > self.limits.max_open_orders_per_bot:
            return RiskTrip(
                code="MAX_OPEN_ORDERS_PER_BOT",
                message=f"bot {runtime.bot_id} has {opens} open orders",
                severity=RiskSeverity.CRITICAL,
            )
        _ = mark
        return None

    def allow_intent(self, runtime: BotRuntime, intent: OrderIntent, mark: Decimal) -> str | None:
        """Return a rejection reason or None if allowed. AI cannot bypass."""
        if self.kill_tripped():
            return "kill switch active"
        if intent.kind.value in {"place", "replace", "flatten"}:
            trip = self.check_bot(runtime, mark)
            if trip:
                return trip.message
        return None
