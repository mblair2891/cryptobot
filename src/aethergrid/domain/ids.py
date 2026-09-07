from __future__ import annotations

from uuid import uuid4

from aethergrid.domain.enums import OrderSide


def client_order_id(bot_id: str, slot_index: int, side: OrderSide, seq: int) -> str:
    """Idempotent-enough id per (bot, slot, side, seq). Coinbase max 128 chars."""
    short = bot_id.replace("bot_", "")[:10]
    nonce = uuid4().hex[:8]
    return f"ag_{short}_{slot_index}_{side}_{seq}_{nonce}"[:128]


def flatten_order_id(bot_id: str, seq: int) -> str:
    short = bot_id.replace("bot_", "")[:10]
    nonce = uuid4().hex[:8]
    return f"ag_{short}_flat_{seq}_{nonce}"[:128]
