from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from aethergrid.vercel_env import on_vercel

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "ui" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter(tags=["ui"])


def _ctx(request: Request, **extra: object) -> dict[str, object]:
    rt = request.app.state.runtime
    ctx: dict[str, object] = {
        "request": request,
        "overview": rt.overview(),
        "mode": rt.settings.mode,
        "live": rt.settings.is_live,
        "has_coinbase_keys": rt.settings.has_coinbase_keys,
        "vercel": on_vercel(),
        "key_status": "present" if rt.settings.has_coinbase_keys else "missing",
    }
    ctx.update(extra)
    return ctx


@router.get("/", response_class=HTMLResponse)
async def overview(request: Request) -> HTMLResponse:
    rt = request.app.state.runtime
    bots = rt.manager.list_runtime()
    fills = await rt.repo.recent_fills(limit=12)
    return templates.TemplateResponse(
        request,
        "overview.html",
        _ctx(request, bots=bots, fills=fills, page="overview"),
    )


@router.get("/bots", response_class=HTMLResponse)
async def bots_page(request: Request) -> HTMLResponse:
    rt = request.app.state.runtime
    return templates.TemplateResponse(
        request,
        "bots.html",
        _ctx(request, bots=rt.manager.list_runtime(), page="bots"),
    )


@router.get("/bots/{bot_id}", response_class=HTMLResponse)
async def bot_detail(request: Request, bot_id: str) -> HTMLResponse:
    rt = request.app.state.runtime
    runtime = rt.manager.runtimes.get(bot_id) or await rt.repo.load_bot(bot_id)
    fills = await rt.repo.recent_fills(limit=50, bot_id=bot_id)
    return templates.TemplateResponse(
        request,
        "bot_detail.html",
        _ctx(request, bot=runtime, fills=fills, page="bots"),
    )


@router.get("/ai", response_class=HTMLResponse)
async def ai_page(request: Request) -> HTMLResponse:
    rt = request.app.state.runtime
    decisions = await rt.repo.recent_ai(limit=40)
    return templates.TemplateResponse(
        request,
        "ai.html",
        _ctx(
            request,
            decisions=decisions,
            operator=rt.operator,
            page="ai",
        ),
    )


@router.get("/journal", response_class=HTMLResponse)
async def journal_page(request: Request) -> HTMLResponse:
    rt = request.app.state.runtime
    fills = await rt.repo.recent_fills(limit=200)
    pnl = await rt.repo.recent_pnl(limit=200)
    return templates.TemplateResponse(
        request,
        "journal.html",
        _ctx(request, fills=fills, pnl=pnl, page="journal"),
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    rt = request.app.state.runtime
    s = rt.settings
    public = {
        "mode": s.mode,
        "live_confirmed": s.live_confirmed,
        "demo": s.mode == "demo",
        "ai_enabled": s.ai_enabled,
        "max_live_notional": s.max_live_notional,
        "max_bots": s.max_bots,
        "max_open_orders_total": s.max_open_orders_total,
        "max_open_orders_per_bot": s.max_open_orders_per_bot,
        "max_daily_realized_loss": s.max_daily_realized_loss,
        "max_drawdown_pct": s.max_drawdown_pct,
        "min_cash_reserve": s.min_cash_reserve,
        "flatten_on_kill": s.flatten_on_kill,
        "has_coinbase_keys": s.has_coinbase_keys,
        "has_llm": s.has_llm,
        "llm_model": s.llm_model if s.has_llm else "—",
        "database": "postgres" if "postgres" in s.database_url else "sqlite",
        "kill_switch_path": str(s.kill_switch_path),
    }
    return templates.TemplateResponse(
        request,
        "settings.html",
        _ctx(request, settings=public, page="settings"),
    )
