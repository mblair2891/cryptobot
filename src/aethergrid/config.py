from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Secrets never leave this object as plain logs."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    mode: Literal["demo", "paper", "live"] = "demo"
    live_confirmed: bool = False

    coinbase_api_key_name: str = ""
    coinbase_api_private_key: SecretStr = SecretStr("")
    coinbase_api_key_file: str = ""

    xai_api_key: SecretStr = SecretStr("")
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://api.x.ai/v1"
    llm_model: str = "grok-4.6"

    database_url: str = "sqlite+aiosqlite:///./data/aethergrid.db"
    data_dir: Path = Path("./data")

    host: str = "0.0.0.0"
    port: int = 8000
    embed_worker: bool = Field(default=True, validation_alias="AETHERGRID_EMBED_WORKER")

    max_live_notional: Decimal = Decimal("500")
    max_bots: int = 5
    max_open_orders_total: int = 200
    max_open_orders_per_bot: int = 80
    max_daily_realized_loss: Decimal = Decimal("50")
    max_drawdown_pct: Decimal = Decimal("0.15")
    max_base_inventory_per_bot: Decimal = Decimal("0")
    min_cash_reserve: Decimal = Decimal("0")
    flatten_on_kill: bool = False
    kill_switch_path: Path = Path("./data/KILL_SWITCH")

    paper_quote_balance: Decimal = Decimal("100000")
    paper_fee_bps: Decimal = Decimal("60")
    paper_quote_currency: str = "USD"

    demo_quote_balance: Decimal = Decimal("10000")
    demo_fee_bps: Decimal = Decimal("60")
    demo_seed_on_boot: bool = True

    ai_enabled: bool = True
    ai_interval_seconds: int = 60
    reconcile_interval_seconds: int = 15
    ticker_poll_seconds: float = 1.0

    log_level: str = "INFO"
    log_json: bool = True

    @field_validator(
        "max_live_notional",
        "max_daily_realized_loss",
        "max_drawdown_pct",
        "max_base_inventory_per_bot",
        "min_cash_reserve",
        "paper_quote_balance",
        "paper_fee_bps",
        "demo_quote_balance",
        "demo_fee_bps",
        mode="before",
    )
    @classmethod
    def _dec(cls, v: object) -> object:
        if v is None or v == "":
            return Decimal("0")
        return v

    @model_validator(mode="after")
    def _paths(self) -> Self:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.kill_switch_path.parent:
            self.kill_switch_path.parent.mkdir(parents=True, exist_ok=True)
        self._apply_runtime_overlay()
        return self

    def _apply_runtime_overlay(self) -> None:
        from aethergrid.vercel_env import on_vercel

        if on_vercel():
            object.__setattr__(self, "mode", "demo")
            object.__setattr__(self, "live_confirmed", False)
            return
        path = self.data_dir / "runtime_mode.json"
        if not path.exists():
            return
        try:
            import json

            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        mode = data.get("mode")
        if mode in {"demo", "paper", "live"}:
            object.__setattr__(self, "mode", mode)
        if "live_confirmed" in data:
            object.__setattr__(self, "live_confirmed", bool(data.get("live_confirmed")))
        if "ai_enabled" in data:
            object.__setattr__(self, "ai_enabled", bool(data.get("ai_enabled")))

    @property
    def is_live(self) -> bool:
        return self.mode == "live" and self.live_confirmed

    @property
    def is_demo(self) -> bool:
        return self.mode == "demo"

    @property
    def virtual_quote_balance(self) -> Decimal:
        if self.mode == "demo":
            return self.demo_quote_balance
        return self.paper_quote_balance

    @property
    def effective_database_url(self) -> str:
        """Demo uses a separate SQLite file so `demo reset` cannot wipe paper/live."""
        default = "sqlite+aiosqlite:///./data/aethergrid.db"
        if self.mode == "demo" and self.database_url == default:
            return "sqlite+aiosqlite:///./data/aethergrid-demo.db"
        return self.database_url

    @property
    def llm_key(self) -> str:
        for secret in (self.xai_api_key, self.llm_api_key):
            value = secret.get_secret_value().strip()
            if value:
                return value
        return ""

    @property
    def has_llm(self) -> bool:
        return bool(self.llm_key)

    @property
    def has_coinbase_keys(self) -> bool:
        name = self.coinbase_api_key_name.strip()
        key = self.coinbase_api_private_key.get_secret_value().strip()
        return bool(name and key) or bool(self.coinbase_api_key_file.strip())

    @property
    def sqlite_path(self) -> Path | None:
        url = self.effective_database_url
        if "sqlite" not in url:
            return None
        if ":///" in url:
            raw = url.split(":///", 1)[1]
            if raw in {":memory:", ""}:
                return None
            return Path(raw)
        return None

    def ensure_live_allowed(self) -> None:
        if self.mode == "demo":
            raise PermissionError(
                "MODE=demo cannot place live orders, even if Coinbase keys are set. "
                "Leave demo, run paper, then confirm live."
            )
        if self.mode != "live":
            raise PermissionError("MODE is not live")
        if not self.live_confirmed:
            raise PermissionError(
                "Live trading requires LIVE_CONFIRMED=true and "
                "`aethergrid live --i-understand-the-risk`"
            )
        if not self.has_coinbase_keys:
            raise PermissionError("Coinbase CDP API credentials are required for live mode")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    get_settings.cache_clear()
