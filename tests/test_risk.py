from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from aethergrid.config import Settings
from aethergrid.domain.enums import RiskSeverity
from aethergrid.risk.limits import HardLimits, RiskEngine


def test_drawdown_trips(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, kill_switch_path=tmp_path / "KILL")
    limits = HardLimits(
        max_live_notional=Decimal("500"),
        max_bots=2,
        max_open_orders_total=10,
        max_open_orders_per_bot=5,
        max_daily_realized_loss=Decimal("50"),
        max_drawdown_pct=Decimal("0.10"),
        max_base_inventory_per_bot=Decimal("0"),
        min_cash_reserve=Decimal("0"),
        flatten_on_kill=False,
    )
    engine = RiskEngine(settings, limits)
    trip = engine.check_portfolio(
        bots=[],
        equity=Decimal("80"),
        starting_equity=Decimal("100"),
        daily_realized=Decimal("0"),
        cash=Decimal("80"),
        live_notional=Decimal("0"),
        open_orders=0,
    )
    assert trip is not None
    assert trip.code == "MAX_DRAWDOWN"
    assert trip.severity == RiskSeverity.KILL


def test_kill_switch_file(tmp_path: Path) -> None:
    path = tmp_path / "KILL"
    path.write_text("stop", encoding="utf-8")
    settings = Settings(data_dir=tmp_path, kill_switch_path=path)
    engine = RiskEngine(settings)
    trip = engine.check_portfolio(
        bots=[],
        equity=Decimal("100"),
        starting_equity=Decimal("100"),
        daily_realized=Decimal("0"),
        cash=Decimal("100"),
        live_notional=Decimal("0"),
        open_orders=0,
    )
    assert trip is not None
    assert trip.code == "KILL_SWITCH"
    assert trip.stop_ai


def test_ai_cannot_disable_limits(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, kill_switch_path=tmp_path / "K")
    engine = RiskEngine(settings)
    assert engine.limits.max_bots == settings.max_bots
    # There is no setter on the operator path that mutates HardLimits.
    assert not hasattr(engine, "disable")
