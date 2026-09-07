from __future__ import annotations

import os
from pathlib import Path

import pytest

from aethergrid.config import Settings, reset_settings
from aethergrid.runtime import AppRuntime
from aethergrid.vercel_env import apply_vercel_demo_env


def test_vercel_env_forces_demo_and_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("MODE", "live")
    monkeypatch.setenv("LIVE_CONFIRMED", "true")
    monkeypatch.setenv("COINBASE_API_KEY_NAME", "organizations/x/apiKeys/y")
    monkeypatch.setenv("COINBASE_API_PRIVATE_KEY", "-----BEGIN EC PRIVATE KEY-----\nX\n-----END EC PRIVATE KEY-----")
    apply_vercel_demo_env(force=True)
    reset_settings()
    settings = Settings()
    assert settings.mode == "demo"
    assert settings.live_confirmed is False
    assert settings.embed_worker is False
    assert settings.effective_database_url.startswith("sqlite+aiosqlite:////tmp/")
    assert not settings.has_coinbase_keys
    assert not settings.is_live


@pytest.mark.asyncio
async def test_tick_once_advances_demo_without_worker(tmp_path: Path) -> None:
    settings = Settings(
        mode="demo",
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/v.db",
        kill_switch_path=tmp_path / "KILL",
        embed_worker=False,
        ai_enabled=False,
        demo_seed_on_boot=True,
        log_json=False,
    )
    rt = await AppRuntime.create(settings)
    try:
        await rt.start()
        assert rt.worker_enabled is False
        assert rt.tasks == []
        before = (await rt.exchange.get_ticker("BTC-USD")).price
        result = await rt.tick_once()
        after = rt.ticks.get("BTC-USD")
        assert result["ok"] is True
        assert result["worker"] is False
        assert after is not None
        _ = before
    finally:
        await rt.stop()


def test_api_index_exports_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("MODE", "demo")
    import importlib

    vercel_entry = importlib.import_module("api.index")
    assert vercel_entry.app is not None
    assert getattr(vercel_entry.app, "title", None) == "AetherGrid"
