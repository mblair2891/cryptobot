from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from aethergrid.config import Settings
from aethergrid.domain.enums import BotStatus, Venue
from aethergrid.exchange.demo import DemoExchange
from aethergrid.runtime import AppRuntime


def _settings(tmp_path: Path, **over: object) -> Settings:
    kwargs: dict[str, object] = dict(
        mode="demo",
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/demo.db",
        kill_switch_path=tmp_path / "KILL",
        embed_worker=False,
        ai_enabled=False,
        demo_seed_on_boot=True,
        demo_quote_balance=Decimal("10000"),
        log_json=False,
    )
    kwargs.update(over)
    return Settings(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_seed_creates_running_paused_journal(tmp_path: Path) -> None:
    rt = await AppRuntime.create(_settings(tmp_path))
    try:
        await rt.start()
        assert isinstance(rt.exchange, DemoExchange)
        bots = {b.config.name: b for b in rt.manager.list_runtime()}
        assert "demo-btc-range" in bots
        assert "demo-eth-trend" in bots
        assert "demo-sol-paused" in bots
        assert bots["demo-btc-range"].status == BotStatus.RUNNING
        assert bots["demo-eth-trend"].status == BotStatus.RUNNING
        assert bots["demo-sol-paused"].status == BotStatus.PAUSED
        assert all(b.venue == Venue.DEMO for b in bots.values())
        fills = await rt.repo.recent_fills(limit=50)
        assert fills
        pnl = await rt.repo.recent_pnl(limit=50)
        assert pnl
        ai = await rt.repo.recent_ai(limit=20)
        assert len(ai) >= 4
        bals = await rt.exchange.get_balances()
        usd = next(b for b in bals if b.currency == "USD")
        assert usd.total > 0
        overview = rt.overview()
        assert overview["mode"] == "demo"
        assert overview["demo"] is True
        assert overview["live"] is False
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_seed_is_idempotent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    rt = await AppRuntime.create(settings)
    try:
        await rt.start()
        n = len(rt.manager.list_runtime())
        from aethergrid.demo.seed import seed_if_empty

        again = await seed_if_empty(rt)
        assert again is False
        assert len(rt.manager.list_runtime()) == n
    finally:
        await rt.stop()
