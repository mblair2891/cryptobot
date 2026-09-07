from __future__ import annotations

from decimal import Decimal

from aethergrid.domain.enums import IntentKind
from aethergrid.domain.models import GridConfig
from aethergrid.strategy.grid import GridEngine


def test_stop_loss_triggers_flatten(engine: GridEngine, grid_config: GridConfig) -> None:
    grid_config.stop_loss_pct = Decimal("0.10")
    grid_config.flatten_on_stop = True
    runtime = engine.initialize(grid_config, Decimal("100000"), Decimal("10000"))
    runtime.inventory.quote = Decimal("1000")
    runtime.inventory.base = Decimal("0")
    intent = engine.check_exits(runtime, Decimal("100000"))
    assert intent is not None
    assert intent.kind == IntentKind.FLATTEN
    assert intent.reason == "stop_loss"


def test_take_profit_on_realized(engine: GridEngine, grid_config: GridConfig) -> None:
    grid_config.take_profit_pct = Decimal("0.05")
    runtime = engine.initialize(grid_config, Decimal("100000"), Decimal("10000"))
    runtime.inventory.realized_pnl = Decimal("600")
    intent = engine.check_exits(runtime, Decimal("100000"))
    assert intent is not None
    assert intent.reason == "take_profit"


def test_no_exit_inside_band(engine: GridEngine, grid_config: GridConfig) -> None:
    runtime = engine.initialize(grid_config, Decimal("100000"), Decimal("10000"))
    assert engine.check_exits(runtime, Decimal("100000")) is None
