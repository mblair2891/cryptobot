from __future__ import annotations

from pathlib import Path

from aethergrid.config import Settings
from aethergrid.logging import get_logger

log = get_logger("security")

# Coinbase Advanced Trade paths that move funds off-exchange or between accounts.
# AetherGrid must never call these — it is non-custodial and trade-only.
FORBIDDEN_PATH_FRAGMENTS = (
    "/withdraw",
    "withdraw",
    "/withdrawals",
    "/transfers",
    "/transfer",
    "transfer",
    "/send",
    "/address-book",
    "/convert",
    "convert",
    "/portfolios/move",
)

FORBIDDEN_SDK_METHODS = {
    "create_convert_quote",
    "commit_convert_trade",
    "get_convert_trade",
}


class SecurityError(RuntimeError):
    pass


def load_coinbase_private_key(settings: Settings) -> str:
    """Load the EC private key from env or file. Never log the material."""
    file_path = settings.coinbase_api_key_file.strip()
    if file_path:
        text = Path(file_path).expanduser().read_text(encoding="utf-8")
        return _normalize_pem(text)
    raw = settings.coinbase_api_private_key.get_secret_value()
    return _normalize_pem(raw)


def _normalize_pem(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    # .env often stores PEM with literal \n sequences.
    if "\\n" in text and "\n" not in text[1:]:
        text = text.replace("\\n", "\n")
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    return text


def assert_no_withdraw_surface(path: str) -> None:
    lowered = path.lower()
    for fragment in FORBIDDEN_PATH_FRAGMENTS:
        if fragment in lowered:
            raise SecurityError(f"Blocked non-trade Coinbase path: {path}")


def assert_trade_only_key_docs() -> None:
    """Runtime reminder — CDP does not expose a permissions introspection call here."""
    log.info(
        "coinbase_key_policy",
        required="view+trade",
        forbidden="transfer/withdraw",
        custody="funds never leave the user Coinbase account",
    )


def kill_switch_tripped(settings: Settings) -> bool:
    path = settings.kill_switch_path
    return path.exists()


def trip_kill_switch(settings: Settings, reason: str) -> None:
    path = settings.kill_switch_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(reason.strip() or "kill", encoding="utf-8")
    log.warning("kill_switch_tripped", reason=reason, path=str(path))


def clear_kill_switch(settings: Settings) -> None:
    path = settings.kill_switch_path
    if path.exists():
        path.unlink()
        log.warning("kill_switch_cleared", path=str(path))


def live_confirmation_path(settings: Settings) -> Path:
    return settings.data_dir / "LIVE_CONFIRMED"


def write_live_confirmation(settings: Settings) -> None:
    path = live_confirmation_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("I UNDERSTAND THE RISK\n", encoding="utf-8")


def live_confirmation_present(settings: Settings) -> bool:
    path = live_confirmation_path(settings)
    if not path.exists():
        return False
    return "I UNDERSTAND THE RISK" in path.read_text(encoding="utf-8")
