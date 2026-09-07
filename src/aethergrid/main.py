from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from aethergrid import __version__
from aethergrid.api.routes_ai import router as ai_router
from aethergrid.api.routes_bots import router as bots_router
from aethergrid.api.routes_health import router as health_router
from aethergrid.api.routes_mode import router as mode_router
from aethergrid.api.routes_ui import router as ui_router
from aethergrid.config import Settings, reset_settings
from aethergrid.runtime import AppRuntime
from aethergrid.vercel_env import apply_vercel_demo_env, on_vercel

STATIC_DIR = Path(__file__).resolve().parent / "ui" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    apply_vercel_demo_env()
    settings: Settings | None = getattr(app.state, "settings", None)
    if settings is None or on_vercel():
        reset_settings()
        settings = Settings()
        app.state.settings = settings
    runtime = await AppRuntime.create(settings)
    app.state.runtime = runtime
    await runtime.start()
    try:
        yield
    finally:
        # Serverless workers are reused; keep /tmp SQLite + in-memory demo book.
        if not on_vercel():
            await runtime.stop()


def create_app(settings: Settings | None = None) -> FastAPI:
    apply_vercel_demo_env()
    app = FastAPI(
        title="AetherGrid",
        version=__version__,
        description="Self-hosted non-custodial AI-operated crypto GRID for Coinbase Advanced Trade.",
        lifespan=lifespan,
    )
    if settings is not None and not on_vercel():
        app.state.settings = settings
    app.include_router(health_router)
    app.include_router(mode_router)
    app.include_router(bots_router)
    app.include_router(ai_router)
    app.include_router(ui_router)

    @app.middleware("http")
    async def _demo_tick_per_request(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        rt = getattr(request.app.state, "runtime", None)
        if (
            rt is not None
            and rt.settings.is_demo
            and not rt.worker_enabled
            and not path.startswith("/static")
            and path not in {"/health", "/api/tick"}
        ):
            try:
                await rt.tick_once(run_ai=False)
            except Exception:  # noqa: BLE001
                pass
        return await call_next(request)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


app = create_app()
