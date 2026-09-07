from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from aethergrid.ai.features import BotFeatures, OperatorFeatures, PairFeatures
from aethergrid.money import ZERO


class Noop(BaseModel):
    action: Literal["noop"] = "noop"
    reason: str = "no edge"


class ProposeNewBot(BaseModel):
    action: Literal["propose_new_bot"] = "propose_new_bot"
    product_id: str
    investment_quote: Decimal
    lower_price: Decimal
    upper_price: Decimal
    grid_levels: int = 21
    trailing_up: bool = True
    trailing_down: bool = False
    take_profit_pct: Decimal | None = Decimal("0.08")
    stop_loss_pct: Decimal | None = Decimal("0.12")
    reason: str


class ReconfigureBot(BaseModel):
    action: Literal["reconfigure_bot"] = "reconfigure_bot"
    bot_id: str
    lower_price: Decimal | None = None
    upper_price: Decimal | None = None
    grid_levels: int | None = None
    trailing_up: bool | None = None
    trailing_down: bool | None = None
    take_profit_pct: Decimal | None = None
    stop_loss_pct: Decimal | None = None
    reason: str


class TrailUp(BaseModel):
    action: Literal["trail_up"] = "trail_up"
    bot_id: str
    reason: str


class TrailDown(BaseModel):
    action: Literal["trail_down"] = "trail_down"
    bot_id: str
    reason: str


class PauseEntries(BaseModel):
    action: Literal["pause_entries"] = "pause_entries"
    bot_id: str
    reason: str


class ResumeBot(BaseModel):
    action: Literal["resume"] = "resume"
    bot_id: str
    reason: str


class AddFunds(BaseModel):
    action: Literal["add_funds"] = "add_funds"
    bot_id: str
    quote_amount: Decimal
    reason: str


class TakeProfitClose(BaseModel):
    action: Literal["take_profit_close"] = "take_profit_close"
    bot_id: str
    reason: str


class StopLossClose(BaseModel):
    action: Literal["stop_loss_close"] = "stop_loss_close"
    bot_id: str
    reason: str


class FlattenAndArchive(BaseModel):
    action: Literal["flatten_and_archive"] = "flatten_and_archive"
    bot_id: str
    reason: str


Action = Annotated[
    Union[
        Noop,
        ProposeNewBot,
        ReconfigureBot,
        TrailUp,
        TrailDown,
        PauseEntries,
        ResumeBot,
        AddFunds,
        TakeProfitClose,
        StopLossClose,
        FlattenAndArchive,
    ],
    Field(discriminator="action"),
]


class ActionEnvelope(BaseModel):
    root: Action


BTC_BETA_PREFIXES = ("BTC", "WBTC", "CBBTC", "TBTC")


def _btc_like(product_id: str) -> bool:
    base = product_id.split("-")[0].upper()
    return base in BTC_BETA_PREFIXES or base.startswith("BTC")


def validate_action(action: Action, features: OperatorFeatures, max_bots: int) -> str | None:
    """Deterministic validator. LLM suggestions must pass this too."""
    if features.kill_switch:
        if getattr(action, "action", "") != "noop":
            return "kill switch active"
    if isinstance(action, ProposeNewBot):
        active = [b for b in features.bots if b.status in {"running", "paused", "starting", "cooldown"}]
        if len(active) >= max_bots:
            return "max bots"
        if action.investment_quote <= 0:
            return "investment must be positive"
        if action.lower_price <= 0 or action.upper_price <= action.lower_price:
            return "invalid range"
        if action.grid_levels < 2:
            return "levels"
        pair = next((p for p in features.pairs if p.product_id == action.product_id), None)
        if pair is None:
            return "unknown product"
        if not pair.usd_like:
            return "non USD/USDC pair rejected"
        if not pair.price_inside_range and not (
            action.lower_price < pair.mark < action.upper_price
        ):
            return "price not inside proposed range"
        if pair.mark <= action.lower_price or pair.mark >= action.upper_price:
            return "price not inside proposed range"
        if not pair.step_ok:
            return "step too tight vs fees/spread"
        if pair.trend_regime:
            return "strong trend — skip static grid"
        if action.investment_quote > features.cash * Decimal("0.4") and features.cash > 0:
            return "per-pair allocation cap 40% of cash"
        btc_exposure = sum(
            Decimal("1")
            for b in active
            if _btc_like(b.product_id)
        )
        if _btc_like(action.product_id) and btc_exposure >= 2:
            return "correlated BTC-beta cap"
        if any(b.product_id == action.product_id for b in active):
            return "pair already running"
    if isinstance(action, AddFunds) and action.quote_amount <= 0:
        return "add_funds amount"
    return None


def propose_range(pair: PairFeatures) -> tuple[Decimal, Decimal]:
    low, high = pair.range_low, pair.range_high
    if low <= 0 or high <= low:
        atr_v = pair.atr_1h or pair.mark * Decimal("0.01")
        low = pair.mark - atr_v * 8
        high = pair.mark + atr_v * 8
    # Pad slightly inside the swing so we are not sitting on extremes.
    width = high - low
    low = low + width * Decimal("0.05")
    high = high - width * Decimal("0.05")
    if low >= high or not (low < pair.mark < high):
        atr_v = pair.atr_1h or pair.mark * Decimal("0.015")
        low = pair.mark * Decimal("0.92")
        high = pair.mark * Decimal("1.08")
    return low, high


def decide(features: OperatorFeatures, *, max_bots: int, quote_budget: Decimal) -> Action:
    if features.kill_switch:
        return Noop(reason="kill switch")

    for bot in features.bots:
        if bot.status in {"archived", "stopped"}:
            continue
        if bot.unrealized + bot.realized <= -Decimal("0.12") * (bot.equity - bot.unrealized or Decimal("1")):
            if bot.inventory_skew >= Decimal("0.8") and not bot.in_range:
                return FlattenAndArchive(
                    bot_id=bot.bot_id,
                    reason="one-sided inventory trending against the grid",
                )
        if not bot.in_range and bot.status == "running":
            if bot.mark > 0:
                return PauseEntries(bot_id=bot.bot_id, reason="price left configured range")
        if bot.breakout and bot.status == "running":
            return TrailUp(bot_id=bot.bot_id, reason="breakout — trail rather than fade")
        if bot.inventory_skew >= Decimal("0.85") and bot.unrealized < 0:
            return StopLossClose(
                bot_id=bot.bot_id,
                reason="inventory skew + adverse unrealized — tighten, do not average forever",
            )
        if bot.status == "paused" and bot.in_range and not bot.breakout and bot.error_count == 0:
            return ResumeBot(bot_id=bot.bot_id, reason="range recovered, resume entries")
        if bot.status == "running" and bot.realized > ZERO and bot.unrealized > ZERO:
            # Let the bot-level TP handle close; no extra action.
            pass

    active = [b for b in features.bots if b.status in {"running", "paused", "starting", "cooldown"}]
    if len(active) >= max_bots:
        return Noop(reason="at max bots")

    ranked = sorted(
        (p for p in features.pairs if p.usd_like and p.price_inside_range and p.step_ok and not p.trend_regime),
        key=lambda p: p.score,
        reverse=True,
    )
    running_pairs = {b.product_id for b in active}
    for pair in ranked:
        if pair.product_id in running_pairs:
            continue
        if pair.oscillation < Decimal("0.12"):
            continue
        lower, upper = propose_range(pair)
        if not (lower < pair.mark < upper):
            continue
        investment = min(quote_budget, features.cash * Decimal("0.2") if features.cash > 0 else quote_budget)
        if investment <= 0:
            return Noop(reason="no cash for new grid")
        return ProposeNewBot(
            product_id=pair.product_id,
            investment_quote=investment,
            lower_price=lower,
            upper_price=upper,
            grid_levels=21,
            trailing_up=True,
            trailing_down=False,
            take_profit_pct=Decimal("0.08"),
            stop_loss_pct=Decimal("0.12"),
            reason=(
                f"oscillating {pair.product_id}: osc={float(pair.oscillation):.2f} "
                f"er={float(pair.er):.2f} inside={pair.price_inside_range} step_ok={pair.step_ok}"
            ),
        )
    return Noop(reason="no pair cleared range/liquidity/fee filters")


def action_to_dict(action: Action) -> dict[str, Any]:
    return action.model_dump(mode="json")
