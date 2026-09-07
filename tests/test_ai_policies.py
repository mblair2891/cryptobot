from __future__ import annotations

from decimal import Decimal

from aethergrid.ai.features import BotFeatures, OperatorFeatures, PairFeatures
from aethergrid.ai.policies import Noop, ProposeNewBot, decide, validate_action


def _pair(**over: object) -> PairFeatures:
    base = dict(
        product_id="ETH-USD",
        quote="USD",
        mark=Decimal("3000"),
        spread_bps=Decimal("2"),
        volume_24h=Decimal("1000000"),
        oscillation=Decimal("0.3"),
        price_inside_range=True,
        step_ok=True,
        liquidity_ok=True,
        usd_like=True,
        trend_regime=False,
        range_low=Decimal("2700"),
        range_high=Decimal("3300"),
        score=Decimal("8"),
    )
    base.update(over)
    return PairFeatures(**base)  # type: ignore[arg-type]


def test_propose_when_pair_oscillates() -> None:
    features = OperatorFeatures(
        pairs=[_pair()],
        cash=Decimal("10000"),
        equity=Decimal("10000"),
        mode="paper",
    )
    action = decide(features, max_bots=5, quote_budget=Decimal("1000"))
    assert isinstance(action, ProposeNewBot)
    assert action.product_id == "ETH-USD"
    assert action.lower_price < Decimal("3000") < action.upper_price
    assert validate_action(action, features, 5) is None


def test_skip_strong_trend() -> None:
    features = OperatorFeatures(pairs=[_pair(trend_regime=True, score=Decimal("1"))], cash=Decimal("10000"))
    action = decide(features, max_bots=5, quote_budget=Decimal("1000"))
    assert isinstance(action, Noop)


def test_validator_rejects_outside_range() -> None:
    features = OperatorFeatures(pairs=[_pair()], cash=Decimal("10000"))
    action = ProposeNewBot(
        product_id="ETH-USD",
        investment_quote=Decimal("1000"),
        lower_price=Decimal("1000"),
        upper_price=Decimal("1100"),
        reason="bad",
    )
    assert validate_action(action, features, 5) == "price not inside proposed range"


def test_validator_kill_switch() -> None:
    features = OperatorFeatures(pairs=[_pair()], cash=Decimal("10000"), kill_switch=True)
    action = ProposeNewBot(
        product_id="ETH-USD",
        investment_quote=Decimal("100"),
        lower_price=Decimal("2700"),
        upper_price=Decimal("3300"),
        reason="x",
    )
    assert validate_action(action, features, 5) == "kill switch active"


def test_flatten_one_sided_inventory() -> None:
    features = OperatorFeatures(
        bots=[
            BotFeatures(
                bot_id="bot_1",
                product_id="ETH-USD",
                status="running",
                mark=Decimal("2000"),
                inventory_skew=Decimal("0.95"),
                realized=Decimal("-50"),
                unrealized=Decimal("-400"),
                equity=Decimal("600"),
                in_range=False,
            )
        ],
        cash=Decimal("1000"),
    )
    action = decide(features, max_bots=5, quote_budget=Decimal("1000"))
    assert action.action in {"flatten_and_archive", "pause_entries", "stop_loss_close"}
