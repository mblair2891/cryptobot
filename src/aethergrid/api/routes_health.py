from __future__ import annotations

from fastapi import APIRouter, Request

from aethergrid import __version__
from aethergrid.security import kill_switch_tripped

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    rt = request.app.state.runtime
    return {
        "ok": True,
        "version": __version__,
        "mode": rt.settings.mode,
        "live": rt.settings.is_live,
        "demo": rt.settings.is_demo,
        "kill_switch": kill_switch_tripped(rt.settings),
        "started": rt.started,
    }


@router.get("/api/status")
async def status(request: Request) -> dict[str, object]:
    return request.app.state.runtime.overview()
