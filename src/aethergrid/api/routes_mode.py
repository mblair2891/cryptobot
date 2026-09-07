from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from aethergrid.mode import ModeError, describe_mode
from aethergrid.vercel_env import on_vercel

router = APIRouter(prefix="/api", tags=["mode"])


class SwitchModeBody(BaseModel):
    target: Literal["demo", "paper", "live"]
    confirmation: str = ""
    cancel_live_orders: bool = True


def _mode_payload(request: Request) -> dict[str, Any]:
    rt = request.app.state.runtime
    s = rt.settings
    info = describe_mode(s)
    info.update(
        {
            "vercel": on_vercel(),
            "key_present": s.has_coinbase_keys,
            "ai_enabled": s.ai_enabled and not rt.operator.paused,
            "live_banner": s.mode == "live",
        }
    )
    return info


@router.get("/mode")
async def get_mode(request: Request) -> dict[str, Any]:
    return _mode_payload(request)


@router.post("/mode")
async def post_mode(request: Request, body: SwitchModeBody) -> dict[str, Any]:
    rt = request.app.state.runtime
    try:
        result = await rt.switch_mode(
            body.target,
            confirmation=body.confirmation,
            cancel_live_orders=body.cancel_live_orders,
        )
    except ModeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(400, str(exc)) from exc
    payload = _mode_payload(request)
    payload.update(result)
    return payload
