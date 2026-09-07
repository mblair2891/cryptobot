from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aethergrid.ai.features import BotFeatures, OperatorFeatures, PairFeatures, pair_features
from aethergrid.ai.policies import Noop, ProposeNewBot, decide, validate_action
from aethergrid.domain.models import Candle, Product, Ticker


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
        crossings_7d=38,
        drift_pct=Decimal("0.04"),
        score=Decimal("8"),
    )
    base.update(over)
    return PairFeatures(**base)  # type: ignore[arg-type]


def _candles_choppy(n: int = 80) -> list[Candle]:
    out: list[Candle] = []
    lo, hi = Decimal("90"), Decimal("110")
    px = Decimal("100")
    direction = Decimal("1")
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(n):
        nxt = px + direction * Decimal("2.5")
        if nxt >= hi:
            nxt, direction = hi, Decimal("-1")
        elif nxt <= lo:
            nxt, direction = lo, Decimal("1")
        top, bot = max(px, nxt), min(px, nxt)
        out.append(
            Candle(
                product_id="SOL-USD",
                start=t0 + timedelta(hours=i),
                open=px,
                high=top,
                low=bot,
                close=nxt,
                volume=Decimal("1000"),
            )
        )
        px = nxt
    return out


def _candles_trend(n: int = 80, start: Decimal = Decimal("100")) -> list[Candle]:
    out: list[Candle] = []
    px = start
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(n):
        nxt = (px * Decimal("1.012")).quantize(Decimal("0.0001"))
        out.append(
            Candle(
                product_id="SOL-USD",
                start=t0 + timedelta(hours=i),
                open=px,
                high=nxt,
                low=px,
                close=nxt,
                volume=Decimal("1000"),
            )
        )
        px = nxt
    return out


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
    assert "crossings" in action.reason
    assert validate_action(action, features, 5) is None


def test_skip_strong_trend() -> None:
    features = OperatorFeatures(
        pairs=[_pair(trend_regime=True, crossings_7d=4, drift_pct=Decimal("0.8"), score=Decimal("1"))],
        cash=Decimal("10000"),
    )
    action = decide(features, max_bots=5, quote_budget=Decimal("1000"))
    assert isinstance(action, Noop)


def test_ai_accepts_choppy_fixture() -> None:
    product = Product(
        product_id="SOL-USD",
        base_currency="SOL",
        quote_currency="USD",
        quote_increment=Decimal("0.01"),
        base_increment=Decimal("0.001"),
        min_market_funds=Decimal("1"),
        price=Decimal("100"),
        volume_24h=Decimal("50000"),
    )
    candles = _candles_choppy()
    mark = candles[-1].close
    ticker = Ticker(product_id="SOL-USD", price=mark, bid=mark * Decimal("0.9998"), ask=mark * Decimal("1.0002"))
    feat = pair_features(product, ticker, candles, candles[::24], Decimal("0.006"))
    assert feat.crossings_7d >= 8
    assert feat.drift_pct < Decimal("0.60")
    assert not feat.trend_regime
    features = OperatorFeatures(pairs=[feat], cash=Decimal("8000"), mode="demo")
    action = decide(features, max_bots=5, quote_budget=Decimal("500"))
    assert isinstance(action, ProposeNewBot)
    assert action.product_id == "SOL-USD"
    assert "crossings" in action.reason


def test_ai_rejects_trending_pair_fixture() -> None:
    product = Product(
        product_id="SOL-USD",
        base_currency="SOL",
        quote_currency="USD",
        quote_increment=Decimal("0.01"),
        base_increment=Decimal("0.001"),
        min_market_funds=Decimal("1"),
        price=Decimal("100"),
        volume_24h=Decimal("50000"),
    )
    candles = _candles_trend()
    mark = candles[-1].close
    ticker = Ticker(product_id="SOL-USD", price=mark, bid=mark, ask=mark)
    feat = pair_features(product, ticker, candles, candles[::24], Decimal("0.006"))
    assert feat.trend_regime or feat.drift_pct >= Decimal("0.60")
    features = OperatorFeatures(pairs=[feat], cash=Decimal("8000"), mode="demo")
    action = decide(features, max_bots=5, quote_budget=Decimal("500"))
    assert action.action in {"noop", "select_universe"}
    if action.action == "propose_new_bot":
        raise AssertionError("trending fixture must not open a grid")


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
