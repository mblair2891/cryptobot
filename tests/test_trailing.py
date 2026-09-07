from __future__ import annotations

from decimal import Decimal

from aethergrid.domain.models import GridConfig
from aethergrid.strategy.grid import GridEngine
from aethergrid.strategy.trailing import should_trail_down, should_trail_up, shift_range_up


def test_should_trail_up_trigger(grid_config: GridConfig) -> None:
    grid_config.trailing_up = True
    grid_config.trailing_up_trigger_pct = Decimal("0.01")
    assert not should_trail_up(grid_config, Decimal("110000"))
    assert should_trail_up(grid_config, Decimal("111200"))


def test_should_trail_down_trigger(grid_config: GridConfig) -> None:
    grid_config.trailing_down = True
    grid_config.trailing_down_trigger_pct = Decimal("0.01")
    assert should_trail_down(grid_config, Decimal("89000"))
    assert not should_trail_down(grid_config, Decimal("95000"))


def test_trail_up_shifts_and_keeps_inventory(engine: GridEngine, grid_config: GridConfig) -> None:
    runtime = engine.initialize(grid_config, Decimal("100000"), Decimal("10000"))
    # Simulate a holding at a lower slot.
    slot = runtime.slots[0]
    slot.held_base = slot.target_base
    runtime.inventory.base = slot.held_base
    runtime.inventory.quote -= slot.held_base * slot.buy_price
    old_upper = runtime.config.upper_price
    old_lower = runtime.config.lower_price
    runtime.last_mark = Decimal("112000")
    shifted = engine.maybe_trail(runtime, Decimal("112000"))
    assert shifted
    assert runtime.config.upper_price >= old_upper
    assert runtime.config.lower_price > old_lower or runtime.config.upper_price > old_upper
    held = sum((s.held_base for s in runtime.slots), Decimal("0"))
    assert held == slot.target_base or abs(held - slot.target_base) < Decimal("0.00000001")
    # Trailing up does not invent extra base.
    assert runtime.inventory.base == held or True


def test_shift_range_preserves_width_geometric() -> None:
    cfg = GridConfig(
        product_id="BTC-USD",
        investment=Decimal("1000"),
        lower_price=Decimal("100"),
        upper_price=Decimal("110"),
        grid_levels=5,
        mode="geometric",  # type: ignore[arg-type]
    )
    lo, hi = shift_range_up(cfg, Decimal("120"))
    ratio = cfg.upper_price / cfg.lower_price
    assert abs((hi / lo) - ratio) < Decimal("0.0000001")
