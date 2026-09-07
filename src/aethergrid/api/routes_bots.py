from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from aethergrid.domain.models import BotRuntime, GridConfig
from aethergrid.market.products import filter_spot

router = APIRouter(prefix="/api", tags=["bots"])


class CreateBotBody(BaseModel):
    product_id: str
    investment: Decimal
    lower_price: Decimal
    upper_price: Decimal
    grid_levels: int = 21
    grid_step_pct: Decimal | None = None
    mode: str = "geometric"
    size_mode: str = "equal_quote"
    start_mode: str = "quote_only"
    trailing_up: bool = False
    trailing_down: bool = False
    take_profit_pct: Decimal | None = None
    stop_loss_pct: Decimal | None = None
    flatten_on_stop: bool = False
    pump_dump_protection: bool = True
    breakout_protection: bool = True
    name: str = ""


class AddFundsBody(BaseModel):
    quote_amount: Decimal


class ReconfigureBody(BaseModel):
    lower_price: Decimal | None = None
    upper_price: Decimal | None = None
    grid_levels: int | None = None
    trailing_up: bool | None = None
    trailing_down: bool | None = None
    take_profit_pct: Decimal | None = None
    stop_loss_pct: Decimal | None = None


def _bot_json(runtime: BotRuntime) -> dict[str, Any]:
    mark = runtime.last_mark
    equity = runtime.inventory.equity(mark) if mark else runtime.inventory.quote
    return {
        "bot_id": runtime.bot_id,
        "product_id": runtime.product_id,
        "name": runtime.config.name,
        "status": runtime.status.value,
        "venue": runtime.venue.value,
        "investment": str(runtime.config.investment),
        "lower_price": str(runtime.config.lower_price),
        "upper_price": str(runtime.config.upper_price),
        "levels": len(runtime.prices),
        "slots": len(runtime.slots),
        "mark": str(mark) if mark else None,
        "base": str(runtime.inventory.base),
        "quote": str(runtime.inventory.quote),
        "realized": str(runtime.inventory.realized_pnl),
        "equity": str(equity),
        "open_orders": [
            {
                "client_order_id": o.client_order_id,
                "side": o.side.value,
                "price": str(o.price),
                "size": str(o.size),
                "filled": str(o.filled_size),
                "status": o.status.value,
            }
            for o in runtime.open_orders.values()
            if o.is_open
        ],
        "grid": [
            {
                "index": s.index,
                "buy": str(s.buy_price),
                "sell": str(s.sell_price),
                "state": s.state.value,
                "held": str(s.held_base),
                "target": str(s.target_base),
            }
            for s in runtime.slots
        ],
        "protection": runtime.protection.model_dump(mode="json"),
        "trailing": runtime.trailing.model_dump(mode="json"),
        "last_error": runtime.last_error,
        "config": runtime.config.model_dump(mode="json"),
    }


@router.get("/products")
async def products(request: Request) -> dict[str, Any]:
    rt = request.app.state.runtime
    tradable = filter_spot(rt.products)
    return {
        "count": len(tradable),
        "products": [
            {
                "product_id": p.product_id,
                "base": p.base_currency,
                "quote": p.quote_currency,
                "status": p.status,
                "price": str(p.price),
                "base_increment": str(p.base_increment),
                "quote_increment": str(p.quote_increment),
                "min_market_funds": str(p.min_market_funds),
                "volume_24h": str(p.volume_24h),
            }
            for p in tradable
        ],
    }


@router.get("/bots")
async def list_bots(request: Request) -> dict[str, Any]:
    rt = request.app.state.runtime
    return {"bots": [_bot_json(b) for b in rt.manager.list_runtime()]}


@router.post("/bots")
async def create_bot(request: Request, body: CreateBotBody) -> dict[str, Any]:
    rt = request.app.state.runtime
    try:
        cfg = GridConfig(
            product_id=body.product_id.upper(),
            investment=body.investment,
            lower_price=body.lower_price,
            upper_price=body.upper_price,
            grid_levels=body.grid_levels,
            grid_step_pct=body.grid_step_pct,
            mode=body.mode,  # type: ignore[arg-type]
            size_mode=body.size_mode,  # type: ignore[arg-type]
            start_mode=body.start_mode,  # type: ignore[arg-type]
            trailing_up=body.trailing_up,
            trailing_down=body.trailing_down,
            take_profit_pct=body.take_profit_pct,
            stop_loss_pct=body.stop_loss_pct,
            flatten_on_stop=body.flatten_on_stop,
            pump_dump_protection=body.pump_dump_protection,
            breakout_protection=body.breakout_protection,
            name=body.name,
        )
        runtime = await rt.manager.create_and_start(cfg)
        return _bot_json(runtime)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc


@router.get("/bots/{bot_id}")
async def show_bot(request: Request, bot_id: str) -> dict[str, Any]:
    rt = request.app.state.runtime
    runtime = rt.manager.runtimes.get(bot_id)
    if not runtime:
        loaded = await rt.repo.load_bot(bot_id)
        if not loaded:
            raise HTTPException(404, "bot not found")
        runtime = loaded
    return _bot_json(runtime)


@router.post("/bots/{bot_id}/pause")
async def pause_bot(request: Request, bot_id: str) -> dict[str, Any]:
    try:
        return _bot_json(await request.app.state.runtime.manager.pause(bot_id))
    except KeyError:
        raise HTTPException(404, "bot not found") from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc


@router.post("/bots/{bot_id}/resume")
async def resume_bot(request: Request, bot_id: str) -> dict[str, Any]:
    try:
        return _bot_json(await request.app.state.runtime.manager.resume(bot_id))
    except KeyError:
        raise HTTPException(404, "bot not found") from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc


@router.post("/bots/{bot_id}/stop")
async def stop_bot(request: Request, bot_id: str, flatten: bool = False) -> dict[str, Any]:
    try:
        return _bot_json(await request.app.state.runtime.manager.stop(bot_id, flatten=flatten))
    except KeyError:
        raise HTTPException(404, "bot not found") from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc


@router.post("/bots/{bot_id}/add-funds")
async def add_funds(request: Request, bot_id: str, body: AddFundsBody) -> dict[str, Any]:
    try:
        return _bot_json(await request.app.state.runtime.manager.add_funds(bot_id, body.quote_amount))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc


@router.post("/bots/{bot_id}/reconfigure")
async def reconfigure(request: Request, bot_id: str, body: ReconfigureBody) -> dict[str, Any]:
    try:
        return _bot_json(
            await request.app.state.runtime.manager.reconfigure(
                bot_id,
                lower=body.lower_price,
                upper=body.upper_price,
                levels=body.grid_levels,
                trailing_up=body.trailing_up,
                trailing_down=body.trailing_down,
                take_profit_pct=body.take_profit_pct,
                stop_loss_pct=body.stop_loss_pct,
            )
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc


@router.post("/kill")
async def kill(request: Request, reason: str = "api") -> dict[str, str]:
    await request.app.state.runtime.engage_kill(reason)
    return {"status": "killed", "reason": reason}


@router.post("/unkill")
async def unkill(request: Request) -> dict[str, str]:
    request.app.state.runtime.clear_kill()
    return {"status": "cleared"}


@router.get("/journal/fills")
async def journal_fills(request: Request, bot_id: str | None = None, limit: int = 200) -> dict[str, Any]:
    rows = await request.app.state.runtime.repo.recent_fills(limit=limit, bot_id=bot_id)
    return {
        "fills": [
            {
                "fill_id": r.fill_id,
                "bot_id": r.bot_id,
                "product_id": r.product_id,
                "side": r.side,
                "price": str(r.price),
                "size": str(r.size),
                "fee": str(r.fee),
                "ts": r.ts.isoformat() if r.ts else None,
            }
            for r in rows
        ]
    }


@router.get("/journal/pnl")
async def journal_pnl(request: Request, bot_id: str | None = None, limit: int = 300) -> dict[str, Any]:
    rows = await request.app.state.runtime.repo.recent_pnl(limit=limit, bot_id=bot_id)
    return {
        "ticks": [
            {
                "bot_id": r.bot_id,
                "product_id": r.product_id,
                "mark": str(r.mark),
                "equity": str(r.equity),
                "realized": str(r.realized),
                "unrealized": str(r.unrealized),
                "ts": r.ts.isoformat() if r.ts else None,
            }
            for r in rows
        ]
    }


@router.get("/settings")
async def public_settings(request: Request) -> dict[str, Any]:
    s = request.app.state.runtime.settings
    return {
        "mode": s.mode,
        "live": s.is_live,
        "demo": s.is_demo,
        "ai_enabled": s.ai_enabled,
        "max_live_notional": str(s.max_live_notional),
        "max_bots": s.max_bots,
        "max_open_orders_total": s.max_open_orders_total,
        "max_open_orders_per_bot": s.max_open_orders_per_bot,
        "max_daily_realized_loss": str(s.max_daily_realized_loss),
        "max_drawdown_pct": str(s.max_drawdown_pct),
        "min_cash_reserve": str(s.min_cash_reserve),
        "flatten_on_kill": s.flatten_on_kill,
        "has_coinbase_keys": s.has_coinbase_keys,
        "has_llm": s.has_llm,
        "llm_model": s.llm_model if s.has_llm else None,
        "database": "postgres" if "postgres" in s.database_url else "sqlite",
    }
