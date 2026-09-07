from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from aethergrid.config import Settings
from aethergrid.exchange.demo import DemoExchange
from aethergrid.mode import ModeError, assert_mode_allowed
from aethergrid.runtime import AppRuntime


def _base(tmp_path: Path, **over: object) -> Settings:
    kwargs: dict[str, object] = dict(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/iso.db",
        kill_switch_path=tmp_path / "KILL",
        embed_worker=False,
        ai_enabled=False,
        demo_seed_on_boot=False,
        log_json=False,
        coinbase_api_key_name="organizations/demo/apiKeys/fake",
        coinbase_api_private_key="-----BEGIN EC PRIVATE KEY-----\nMIIB\n-----END EC PRIVATE KEY-----",
        live_confirmed=False,
        mode="demo",
    )
    kwargs.update(over)
    return Settings(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_demo_never_constructs_coinbase_even_with_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    constructed: list[str] = []

    class Boom:
        def __init__(self, *args: object, **kwargs: object) -> None:
            constructed.append("CoinbaseExchange")
            raise AssertionError("live client must never be constructed in demo")

    monkeypatch.setattr("aethergrid.exchange.coinbase.CoinbaseExchange", Boom)
    settings = _base(tmp_path, mode="demo")
    rt = await AppRuntime.create(settings)
    try:
        await rt.start()
        assert constructed == []
        assert isinstance(rt.exchange, DemoExchange)
        assert rt.exchange.venue.value == "demo"
        assert rt.market is None
        with pytest.raises(ModeError, match="I UNDERSTAND THE RISK"):
            await rt.switch_mode("live", confirmation="nope")
        assert constructed == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_switch_live_blocked_without_keys(tmp_path: Path) -> None:
    settings = _base(tmp_path, mode="demo", coinbase_api_key_name="", coinbase_api_private_key="")
    rt = await AppRuntime.create(settings)
    try:
        await rt.start()
        with pytest.raises(ModeError, match="server env"):
            await rt.switch_mode("live", confirmation="I UNDERSTAND THE RISK")
        assert isinstance(rt.exchange, DemoExchange)
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_vercel_refuses_live_toggle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    settings = _base(tmp_path, mode="demo")
    rt = await AppRuntime.create(settings)
    try:
        await rt.start()
        with pytest.raises(ModeError, match="Vercel"):
            await rt.switch_mode("live", confirmation="I UNDERSTAND THE RISK")
        assert isinstance(rt.exchange, DemoExchange)
    finally:
        await rt.stop()


def test_ensure_live_blocked_in_demo(tmp_path: Path) -> None:
    settings = _base(tmp_path, mode="demo")
    with pytest.raises(PermissionError, match="demo"):
        settings.ensure_live_allowed()


def test_assert_live_needs_confirmation(tmp_path: Path) -> None:
    settings = _base(tmp_path, mode="live", live_confirmed=False)
    with pytest.raises(PermissionError, match="LIVE_CONFIRMED"):
        assert_mode_allowed(settings)


def test_virtual_balance_and_demo_db_split(tmp_path: Path) -> None:
    demo = Settings(mode="demo", data_dir=tmp_path, demo_quote_balance=Decimal("10000"))
    assert demo.virtual_quote_balance == Decimal("10000")
    assert "aethergrid-demo.db" in demo.effective_database_url
    paper = Settings(
        mode="paper",
        data_dir=tmp_path,
        paper_quote_balance=Decimal("100000"),
        database_url="sqlite+aiosqlite:///./data/aethergrid.db",
    )
    assert paper.virtual_quote_balance == Decimal("100000")
    assert paper.effective_database_url.endswith("aethergrid.db")
    assert "demo" not in Path(paper.effective_database_url).name
