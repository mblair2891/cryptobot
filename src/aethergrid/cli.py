from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from pathlib import Path
from typing import Optional

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from aethergrid import __version__
from aethergrid.config import Settings, reset_settings
from aethergrid.mode import describe_mode
from aethergrid.security import live_confirmation_present, write_live_confirmation

app = typer.Typer(no_args_is_help=True, add_completion=False, help="AetherGrid control plane")
bots_app = typer.Typer(help="Bot lifecycle")
ai_app = typer.Typer(help="AI operator")
demo_app = typer.Typer(
    help="Demo mode (synthetic market, no Coinbase). Run with no subcommand to start.",
    invoke_without_command=True,
)
mode_app = typer.Typer(help="Show and inspect trading mode.")
app.add_typer(bots_app, name="bots")
app.add_typer(ai_app, name="ai")
app.add_typer(demo_app, name="demo")
app.add_typer(mode_app, name="mode")
console = Console()


def _settings() -> Settings:
    reset_settings()
    return Settings()


@app.command()
def version() -> None:
    console.print(f"aethergrid {__version__}")


@app.command()
def init() -> None:
    """Create data directory, .env, and SQLite schema."""
    settings = _settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    env_src = Path(".env.example")
    env_dst = Path(".env")
    if env_src.exists() and not env_dst.exists():
        env_dst.write_text(env_src.read_text(encoding="utf-8"), encoding="utf-8")
        console.print("Wrote .env from .env.example")
    from aethergrid.persistence.db import get_engine, init_db

    async def _go() -> None:
        engine = get_engine(settings)
        await init_db(engine)
        await engine.dispose()

    asyncio.run(_go())
    console.print(f"Data dir {settings.data_dir} ready. Default MODE=demo.")


@app.command()
def serve(
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> None:
    """Run the FastAPI control plane (embeds the worker by default)."""
    settings = _settings()
    uvicorn.run(
        "aethergrid.main:app",
        host=host or settings.host,
        port=port or settings.port,
        reload=False,
        log_level=settings.log_level.lower(),
    )


@demo_app.callback(invoke_without_command=True)
def demo_root(ctx: typer.Context) -> None:
    """Start demo mode (synthetic funds and prices; not connected to Coinbase)."""
    if ctx.invoked_subcommand is not None:
        return
    os.environ["MODE"] = "demo"
    os.environ["LIVE_CONFIRMED"] = "false"
    reset_settings()
    settings = _settings()
    console.print(
        "[bold]DEMO[/bold] — simulated funds and prices. Not connected to Coinbase. "
        "Keys, if present, are ignored."
    )
    serve(host=settings.host, port=settings.port)


@demo_app.command("reset")
def demo_reset() -> None:
    """Wipe the demo SQLite database. Next `aethergrid demo` reseeds."""
    os.environ["MODE"] = "demo"
    reset_settings()
    from aethergrid.demo.seed import wipe_demo_database

    settings = _settings()
    try:
        removed = wipe_demo_database(settings)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if removed:
        console.print("Removed: " + ", ".join(str(p) for p in removed))
    else:
        console.print("No demo database file present.")
    console.print("Run `aethergrid demo` to reseed.")


@mode_app.command("show")
def mode_show() -> None:
    """Print the active mode and promotion rules (no secrets)."""
    info = describe_mode(_settings())
    table = Table(title="AetherGrid mode")
    table.add_column("key")
    table.add_column("value")
    for key, value in info.items():
        table.add_row(str(key), str(value))
    console.print(table)


@app.command()
def paper() -> None:
    """Start in paper mode against public Coinbase market data."""
    os.environ["MODE"] = "paper"
    os.environ["LIVE_CONFIRMED"] = "false"
    reset_settings()
    settings = _settings()
    console.print("Starting AetherGrid in PAPER mode. No live orders will be placed.")
    serve(host=settings.host, port=settings.port)


@app.command("live")
def live_cmd(
    i_understand_the_risk: bool = typer.Option(
        False,
        "--i-understand-the-risk",
        help="Required acknowledgement that live trading can lose money.",
    ),
) -> None:
    """Enable live trading only with an explicit flag AND typed confirmation."""
    if not i_understand_the_risk:
        raise typer.BadParameter("Pass --i-understand-the-risk")
    settings = _settings()
    if settings.mode == "demo":
        console.print(
            "[red]MODE=demo cannot be promoted to live.[/red] "
            "Set MODE=paper, run paper once, then `aethergrid live --i-understand-the-risk`."
        )
        raise typer.Exit(1)
    phrase = typer.prompt("Type I UNDERSTAND THE RISK to continue")
    if phrase.strip() != "I UNDERSTAND THE RISK":
        raise typer.Abort()
    write_live_confirmation(settings)
    console.print("[red]Live confirmation written. Set MODE=live and LIVE_CONFIRMED=true in .env[/red]")
    console.print("Key permissions must be view+trade only. Never enable transfer/withdraw.")
    if not live_confirmation_present(settings):
        raise typer.Exit(1)
    console.print("Restart with MODE=live LIVE_CONFIRMED=true, then `aethergrid serve`.")


@app.command()
def worker() -> None:
    """Standalone worker (use when AETHERGRID_EMBED_WORKER=false)."""
    from aethergrid.runtime import AppRuntime

    async def _run() -> None:
        settings = _settings()
        settings.embed_worker = True
        rt = await AppRuntime.create(settings)
        await rt.start()
        console.print("Worker running. Ctrl+C to stop.")
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await rt.stop()

    asyncio.run(_run())


@bots_app.command("list")
def bots_list() -> None:
    from aethergrid.persistence.db import get_engine, get_session_factory, init_db
    from aethergrid.persistence.repo import Repository

    async def _go() -> None:
        settings = _settings()
        engine = get_engine(settings)
        await init_db(engine)
        repo = Repository(get_session_factory(engine))
        bots = await repo.list_bots(include_archived=True)
        table = Table(title="Bots")
        table.add_column("id")
        table.add_column("pair")
        table.add_column("status")
        table.add_column("range")
        for b in bots:
            table.add_row(
                b.bot_id,
                b.product_id,
                b.status.value,
                f"{b.config.lower_price}-{b.config.upper_price}",
            )
        console.print(table)
        await engine.dispose()

    asyncio.run(_go())


@bots_app.command("show")
def bots_show(bot_id: str) -> None:
    from aethergrid.persistence.db import get_engine, get_session_factory, init_db
    from aethergrid.persistence.repo import Repository

    async def _go() -> None:
        settings = _settings()
        engine = get_engine(settings)
        await init_db(engine)
        repo = Repository(get_session_factory(engine))
        bot = await repo.load_bot(bot_id)
        if not bot:
            console.print("not found")
            raise typer.Exit(1)
        console.print(bot.model_dump(mode="json"))
        await engine.dispose()

    asyncio.run(_go())


@bots_app.command("stop")
def bots_stop(bot_id: str) -> None:
    console.print(
        f"Use the running control plane: curl -X POST http://127.0.0.1:8000/api/bots/{bot_id}/stop"
    )


@app.command()
def backtest(
    product: str = typer.Option("BTC-USD", "--product"),
    days: int = typer.Option(90, "--days"),
    investment: str = typer.Option("10000", "--investment"),
) -> None:
    from aethergrid.backtest.engine import run_backtest

    async def _go() -> None:
        report = await run_backtest(
            product_id=product.upper(),
            days=days,
            investment=Decimal(investment),
        )
        console.print(report.model_dump(mode="json"))
        console.print(f"[yellow]{report.caveat}[/yellow]")

    asyncio.run(_go())


@ai_app.command("status")
def ai_status() -> None:
    console.print("Query the running process: curl http://127.0.0.1:8000/api/ai/status")


@app.command()
def logs() -> None:
    console.print("Structured JSON logs go to stdout. Tail your process or `docker compose logs -f`.")


if __name__ == "__main__":
    app()
