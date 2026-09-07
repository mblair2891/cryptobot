from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from aethergrid.config import Settings
from aethergrid.logging import get_logger

log = get_logger("mode")

ModeName = Literal["demo", "paper", "live"]


class ModeError(PermissionError):
    pass


def ledger_path(settings: Settings) -> Path:
    return settings.data_dir / "mode_ledger.json"


def runtime_mode_path(settings: Settings) -> Path:
    return settings.data_dir / "runtime_mode.json"


def persist_runtime_mode(
    settings: Settings,
    *,
    mode: str,
    live_confirmed: bool,
    ai_enabled: bool,
) -> None:
    path = runtime_mode_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "mode": mode,
                "live_confirmed": live_confirmed,
                "ai_enabled": ai_enabled,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def load_runtime_mode(settings: Settings) -> dict[str, Any] | None:
    path = runtime_mode_path(settings)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_ledger(settings: Settings) -> dict[str, Any]:
    path = ledger_path(settings)
    if not path.exists():
        return {"last_mode": None, "seen_paper": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"last_mode": None, "seen_paper": False}
    if not isinstance(data, dict):
        return {"last_mode": None, "seen_paper": False}
    return {
        "last_mode": data.get("last_mode"),
        "seen_paper": bool(data.get("seen_paper")),
    }


def save_ledger(settings: Settings, ledger: dict[str, Any]) -> None:
    path = ledger_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")


def record_mode(settings: Settings) -> dict[str, Any]:
    ledger = load_ledger(settings)
    ledger["last_mode"] = settings.mode
    if settings.mode == "paper":
        ledger["seen_paper"] = True
    save_ledger(settings, ledger)
    return ledger


def assert_mode_allowed(settings: Settings) -> None:
    """Enforce mutually exclusive modes and demo ↛ live promotion."""
    if settings.mode == "demo":
        # Keys may exist in the environment; they are ignored and must never
        # construct a live Coinbase client.
        log.info("mode_demo_credentials_ignored", has_coinbase_keys=settings.has_coinbase_keys)
        record_mode(settings)
        return
    if settings.mode == "paper":
        record_mode(settings)
        return
    if settings.mode == "live":
        settings.ensure_live_allowed()
        record_mode(settings)
        return
    raise ModeError(f"unknown mode {settings.mode}")


def describe_mode(settings: Settings) -> dict[str, Any]:
    ledger = load_ledger(settings)
    return {
        "mode": settings.mode,
        "live_confirmed": settings.live_confirmed,
        "is_live": settings.is_live,
        "is_demo": settings.is_demo,
        "has_coinbase_keys": settings.has_coinbase_keys,
        "has_llm": settings.has_llm,
        "seen_paper": bool(ledger.get("seen_paper")),
        "last_mode": ledger.get("last_mode"),
        "database": settings.effective_database_url.split(":///")[-1],
        "promotion": {
            "demo_to_paper": "allowed",
            "demo_to_live": "blocked until server CDP keys + I UNDERSTAND THE RISK",
            "paper_to_live": "requires I UNDERSTAND THE RISK + LIVE_CONFIRMED",
        },
    }
