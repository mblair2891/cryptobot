from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from aethergrid.config import Settings
from aethergrid.exchange.demo import DemoExchange
from aethergrid.mode import ModeError, assert_mode_allowed, record_mode
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
        live_confirmed=True,
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
    finally:
        await rt.stop()


def test_demo_cannot_promote_straight_to_live(tmp_path: Path) -> None:
    demo = _base(tmp_path, mode="demo")
    assert_mode_allowed(demo)
    live = _base(tmp_path, mode="live")
    with pytest.raises(ModeError, match="paper"):
        assert_mode_allowed(live)


def test_paper_then_live_promotion_allowed(tmp_path: Path) -> None:
    paper = _base(tmp_path, mode="paper", live_confirmed=False)
    record_mode(paper)
    live = _base(tmp_path, mode="live", live_confirmed=True)
    assert_mode_allowed(live)


def test_ensure_live_blocked_in_demo(tmp_path: Path) -> None:
    settings = _base(tmp_path, mode="demo")
    with pytest.raises(PermissionError, match="demo"):
        settings.ensure_live_allowed()


@pytest.mark.asyncio
async def test_runtime_live_without_paper_raises(tmp_path: Path) -> None:
    settings = _base(tmp_path, mode="live")
    with pytest.raises(PermissionError, match="paper"):
        await AppRuntime.create(settings)


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
