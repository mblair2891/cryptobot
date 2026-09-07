from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from aethergrid.persistence.db import Base

JSONType = JSON().with_variant(SQLITE_JSON(), "sqlite")


class BotRow(Base):
    __tablename__ = "bots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    venue: Mapped[str] = mapped_column(String(16), default="paper")
    name: Mapped[str] = mapped_column(String(128), default="")
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONType)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSONType)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrderRow(Base):
    __tablename__ = "orders"

    client_order_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    bot_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    price: Mapped[Any] = mapped_column(Numeric(36, 18))
    size: Mapped[Any] = mapped_column(Numeric(36, 18))
    filled_size: Mapped[Any] = mapped_column(Numeric(36, 18), default=0)
    fee: Mapped[Any] = mapped_column(Numeric(36, 18), default=0)
    status: Mapped[str] = mapped_column(String(24), index=True)
    tif: Mapped[str] = mapped_column(String(8), default="GTC")
    slot_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    venue: Mapped[str] = mapped_column(String(16), default="paper")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FillRow(Base):
    __tablename__ = "fills"

    fill_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(128), index=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    price: Mapped[Any] = mapped_column(Numeric(36, 18))
    size: Mapped[Any] = mapped_column(Numeric(36, 18))
    fee: Mapped[Any] = mapped_column(Numeric(36, 18), default=0)
    venue: Mapped[str] = mapped_column(String(16), default="paper")
    liquidity: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    slot_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PnLTickRow(Base):
    __tablename__ = "pnl_ticks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[str] = mapped_column(String(32))
    mark: Mapped[Any] = mapped_column(Numeric(36, 18))
    equity: Mapped[Any] = mapped_column(Numeric(36, 18))
    realized: Mapped[Any] = mapped_column(Numeric(36, 18))
    unrealized: Mapped[Any] = mapped_column(Numeric(36, 18))
    fees: Mapped[Any] = mapped_column(Numeric(36, 18))
    base_inventory: Mapped[Any] = mapped_column(Numeric(36, 18))
    quote_inventory: Mapped[Any] = mapped_column(Numeric(36, 18))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AIDecisionRow(Base):
    __tablename__ = "ai_decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action: Mapped[str] = mapped_column(String(48), index=True)
    executed: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(Text, default="")
    features_json: Mapped[dict[str, Any]] = mapped_column(JSONType)
    action_json: Mapped[dict[str, Any]] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RiskEventRow(Base):
    __tablename__ = "risk_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text)
    bot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class GridSnapshotRow(Base):
    __tablename__ = "grid_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[str] = mapped_column(String(64), index=True)
    mark: Mapped[Any] = mapped_column(Numeric(36, 18))
    state_json: Mapped[dict[str, Any]] = mapped_column(JSONType)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
