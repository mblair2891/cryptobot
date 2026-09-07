from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from aethergrid.domain.enums import OrderSide, Venue
from aethergrid.domain.models import Fill, GridConfig, new_id
from aethergrid.logging import get_logger
from aethergrid.money import ZERO

if TYPE_CHECKING:
    from aethergrid.runtime import AppRuntime

log = get_logger("demo.seed")

BTC_BOT = "bot_demo_btc_range"
ETH_BOT = "bot_demo_eth_dump"
SOL_BOT = "bot_demo_sol_pause"


def wipe_demo_database(settings) -> list[Path]:
    """Delete the demo SQLite file (and WAL/SHM). No-op for non-sqlite."""
    from aethergrid.config import Settings

    settings = settings if isinstance(settings, Settings) else settings
    removed: list[Path] = []
    path = settings.sqlite_path
    if path is None:
        raise RuntimeError("demo reset only supports SQLite (default demo DB)")
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if candidate.exists():
            candidate.unlink()
            removed.append(candidate)
    log.warning("demo_db_wiped", files=[str(p) for p in removed])
    return removed


async def seed_if_empty(runtime: AppRuntime, *, force: bool = False) -> bool:
    existing = await runtime.repo.list_bots(include_archived=True)
    if existing and not force:
        log.info("demo_seed_skipped", bots=len(existing))
        return False
    await seed_demo(runtime)
    return True


async def seed_demo(runtime: AppRuntime) -> None:
    """Preload a desk that already has winners, losers, and an AI paper trail."""
    from aethergrid.exchange.demo import DemoExchange

    exchange = runtime.exchange
    if not isinstance(exchange, DemoExchange):
        raise RuntimeError("demo seed requires DemoExchange")

    exchange.seed_balance("USD", runtime.settings.demo_quote_balance)
    products = {p.product_id: p for p in await exchange.list_products()}
    runtime.products = list(products.values())
    runtime.manager.products = products

    btc_px = (await exchange.get_ticker("BTC-USD")).last
    eth_px = (await exchange.get_ticker("ETH-USD")).last
    sol_px = (await exchange.get_ticker("SOL-USD")).last

    btc = await runtime.manager.create_and_start(
        GridConfig(
            product_id="BTC-USD",
            investment=Decimal("2500"),
            lower_price=_below(btc_px, Decimal("0.04")),
            upper_price=_above(btc_px, Decimal("0.04")),
            grid_levels=13,
            trailing_up=True,
            take_profit_pct=Decimal("0.08"),
            stop_loss_pct=Decimal("0.18"),
            name="demo-btc-range",
        ),
        mark=btc_px,
        bot_id=BTC_BOT,
    )
    eth = await runtime.manager.create_and_start(
        GridConfig(
            product_id="ETH-USD",
            investment=Decimal("1500"),
            lower_price=_below(eth_px, Decimal("0.03")),
            upper_price=_above(eth_px, Decimal("0.025")),
            grid_levels=11,
            trailing_up=False,
            trailing_down=False,
            take_profit_pct=Decimal("0.06"),
            stop_loss_pct=Decimal("0.35"),
            flatten_on_stop=True,
            name="demo-eth-trend",
        ),
        mark=eth_px,
        bot_id=ETH_BOT,
    )
    sol = await runtime.manager.create_and_start(
        GridConfig(
            product_id="SOL-USD",
            investment=Decimal("1000"),
            lower_price=_below(sol_px, Decimal("0.05")),
            upper_price=_above(sol_px, Decimal("0.05")),
            grid_levels=9,
            trailing_up=True,
            name="demo-sol-paused",
        ),
        mark=sol_px,
        bot_id=SOL_BOT,
    )
    await runtime.manager.pause(sol.bot_id)

    await _drive(runtime, "BTC-USD", steps=18)
    await _drive(runtime, "ETH-USD", steps=24)

    await _journal_history(runtime, btc.bot_id, eth.bot_id)
    log.info(
        "demo_seeded",
        equity=str(runtime.settings.demo_quote_balance),
        bots=[btc.bot_id, eth.bot_id, sol.bot_id],
    )


def _below(mark: Decimal, pct: Decimal) -> Decimal:
    return (mark * (Decimal("1") - pct)).quantize(Decimal("0.01"))


def _above(mark: Decimal, pct: Decimal) -> Decimal:
    return (mark * (Decimal("1") + pct)).quantize(Decimal("0.01"))


async def _drive(runtime: AppRuntime, product_id: str, steps: int) -> None:
    from aethergrid.exchange.demo import DemoExchange

    exchange = runtime.exchange
    assert isinstance(exchange, DemoExchange)
    for _ in range(steps):
        ticker = await exchange.get_ticker(product_id)
        runtime.ticks[product_id] = ticker
        await runtime.manager.on_ticker(ticker)
        fills = await exchange.on_ticker(ticker)
        for fill in fills:
            await runtime.manager.on_fill(fill)


async def _journal_history(runtime: AppRuntime, btc_id: str, eth_id: str) -> None:
    now = datetime.now(UTC)
    btc = runtime.manager.runtimes.get(btc_id)
    mark = btc.last_mark if btc else Decimal("80000")
    samples = [
        (Decimal("0.004"), Decimal("12.40"), Decimal("-3.10")),
        (Decimal("0.003"), Decimal("28.15"), Decimal("4.20")),
        (Decimal("0.002"), Decimal("41.90"), Decimal("6.80")),
        (Decimal("0.001"), Decimal("55.25"), Decimal("2.15")),
    ]
    for i, (ret, realized, unreal) in enumerate(samples):
        ts_mark = mark * (Decimal("1") - ret)
        await runtime.repo.save_pnl(
            bot_id=btc_id,
            product_id="BTC-USD",
            mark=ts_mark,
            equity=Decimal("10000") + realized + unreal,
            realized=realized,
            unrealized=unreal,
            fees=Decimal("1.20") * Decimal(i + 1),
            base_inventory=Decimal("0.01") * Decimal(i),
            quote_inventory=Decimal("7000") - Decimal(i) * Decimal("80"),
        )
        # overwrite ts by inserting fills with explicit past times
        fill = Fill(
            fill_id=new_id("fill"),
            client_order_id=f"ag_demo_hist_{i}",
            product_id="BTC-USD",
            side=OrderSide.BUY if i % 2 == 0 else OrderSide.SELL,
            price=ts_mark,
            size=Decimal("0.002"),
            fee=Decimal("0.96"),
            bot_id=btc_id,
            venue=Venue.DEMO,
            ts=now - timedelta(hours=12 - i * 2),
        )
        await runtime.repo.save_fill(fill)

    decisions = [
        ("propose_new_bot", True, "BTC-USD oscillating; step covers 2x fee+spread", btc_id),
        ("propose_new_bot", True, "ETH-USD inside range at start — later dumped", eth_id),
        ("noop", False, "SOL-USD paused by operator; waiting for range quality", None),
        ("pause_entries", True, "ETH inventory skew + adverse drift — pause new buys", eth_id),
        ("trail_up", False, "validator: BTC still inside original range", btc_id),
        ("stop_loss_close", False, "ETH drawdown approaching stop — watching", eth_id),
    ]
    for i, (action, executed, reason, bot_id) in enumerate(decisions):
        await runtime.repo.save_ai_decision(
            decision_id=new_id("ai"),
            action=action,
            executed=executed,
            reason=reason,
            features={"mode": "demo", "seed": True, "bot_id": bot_id},
            action_payload={"action": action, "bot_id": bot_id, "reason": reason},
        )
    _ = ZERO
