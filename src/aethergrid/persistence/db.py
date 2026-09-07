from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from aethergrid.config import Settings
from aethergrid.logging import get_logger

log = get_logger("db")


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_dir(url: str) -> None:
    if "sqlite" in url and ":///" in url:
        raw = url.split(":///", 1)[1]
        path = Path(raw)
        if path.parent and str(path.parent) not in {".", ""}:
            path.parent.mkdir(parents=True, exist_ok=True)


def get_engine(settings: Settings) -> AsyncEngine:
    url = settings.effective_database_url
    _ensure_sqlite_dir(url)
    kwargs: dict[str, object] = {"echo": False, "future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"timeout": 30}
    return create_async_engine(url, **kwargs)


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db(engine: AsyncEngine) -> None:
    from aethergrid.persistence import models as _models  # noqa: F401

    async with engine.begin() as conn:
        if engine.url.get_backend_name().startswith("sqlite"):
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        await conn.run_sync(Base.metadata.create_all)
    log.info("db_ready", url=str(engine.url).split("@")[-1])


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
