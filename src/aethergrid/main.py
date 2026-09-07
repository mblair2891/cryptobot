from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from aethergrid import __version__
from aethergrid.api.routes_ai import router as ai_router
from aethergrid.api.routes_bots import router as bots_router
from aethergrid.api.routes_health import router as health_router
from aethergrid.api.routes_ui import router as ui_router
from aethergrid.config import Settings
from aethergrid.runtime import AppRuntime

STATIC_DIR = Path(__file__).resolve().parent / "ui" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = getattr(app.state, "settings", None) or Settings()
    runtime = await AppRuntime.create(settings)
    app.state.runtime = runtime
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(
        title="AetherGrid",
        version=__version__,
        description="Self-hosted non-custodial AI-operated crypto GRID for Coinbase Advanced Trade.",
        lifespan=lifespan,
    )
    if settings is not None:
        app.state.settings = settings
    app.include_router(health_router)
    app.include_router(bots_router)
    app.include_router(ai_router)
    app.include_router(ui_router)
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


app = create_app()
