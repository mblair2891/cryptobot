from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/status")
async def ai_status(request: Request) -> dict[str, Any]:
    op = request.app.state.runtime.operator
    return {
        "enabled": op.enabled and not op.paused,
        "paused": op.paused,
        "last_at": op.last_at.isoformat() if op.last_at else None,
        "last_action": op.last_action.model_dump(mode="json") if op.last_action else None,
        "last_error": op.last_error,
        "has_llm": request.app.state.runtime.settings.has_llm,
    }


@router.get("/decisions")
async def decisions(request: Request, limit: int = 50) -> dict[str, Any]:
    rows = await request.app.state.runtime.repo.recent_ai(limit=limit)
    return {
        "decisions": [
            {
                "id": r.id,
                "action": r.action,
                "executed": bool(r.executed),
                "reason": r.reason,
                "error": r.error,
                "payload": r.action_json,
                "ts": r.ts.isoformat() if r.ts else None,
            }
            for r in rows
        ]
    }


@router.post("/pause")
async def pause_ai(request: Request) -> dict[str, str]:
    request.app.state.runtime.operator.paused = True
    return {"status": "paused"}


@router.post("/resume")
async def resume_ai(request: Request) -> dict[str, str]:
    request.app.state.runtime.operator.paused = False
    return {"status": "resumed"}


@router.post("/step")
async def step_ai(request: Request) -> dict[str, Any]:
    rt = request.app.state.runtime
    action = await rt.operator.step(rt.products, rt.ticks)
    return action.model_dump(mode="json")
