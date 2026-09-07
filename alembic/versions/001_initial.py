"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-09-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bots",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("product_id", sa.String(32), nullable=False, index=True),
        sa.Column("status", sa.String(24), nullable=False, index=True),
        sa.Column("venue", sa.String(16), nullable=False, server_default="paper"),
        sa.Column("name", sa.String(128), nullable=False, server_default=""),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "orders",
        sa.Column("client_order_id", sa.String(128), primary_key=True),
        sa.Column("exchange_order_id", sa.String(128), nullable=True, index=True),
        sa.Column("bot_id", sa.String(64), nullable=False, index=True),
        sa.Column("product_id", sa.String(32), nullable=False, index=True),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("price", sa.Numeric(36, 18), nullable=False),
        sa.Column("size", sa.Numeric(36, 18), nullable=False),
        sa.Column("filled_size", sa.Numeric(36, 18), nullable=False, server_default="0"),
        sa.Column("fee", sa.Numeric(36, 18), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, index=True),
        sa.Column("tif", sa.String(8), nullable=False, server_default="GTC"),
        sa.Column("slot_index", sa.Integer(), nullable=True),
        sa.Column("venue", sa.String(16), nullable=False, server_default="paper"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "fills",
        sa.Column("fill_id", sa.String(64), primary_key=True),
        sa.Column("client_order_id", sa.String(128), nullable=False, index=True),
        sa.Column("exchange_order_id", sa.String(128), nullable=True),
        sa.Column("bot_id", sa.String(64), nullable=True, index=True),
        sa.Column("product_id", sa.String(32), nullable=False, index=True),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("price", sa.Numeric(36, 18), nullable=False),
        sa.Column("size", sa.Numeric(36, 18), nullable=False),
        sa.Column("fee", sa.Numeric(36, 18), nullable=False, server_default="0"),
        sa.Column("venue", sa.String(16), nullable=False, server_default="paper"),
        sa.Column("liquidity", sa.String(16), nullable=False, server_default="UNKNOWN"),
        sa.Column("slot_index", sa.Integer(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), index=True),
    )
    op.create_table(
        "pnl_ticks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bot_id", sa.String(64), nullable=False, index=True),
        sa.Column("product_id", sa.String(32), nullable=False),
        sa.Column("mark", sa.Numeric(36, 18), nullable=False),
        sa.Column("equity", sa.Numeric(36, 18), nullable=False),
        sa.Column("realized", sa.Numeric(36, 18), nullable=False),
        sa.Column("unrealized", sa.Numeric(36, 18), nullable=False),
        sa.Column("fees", sa.Numeric(36, 18), nullable=False),
        sa.Column("base_inventory", sa.Numeric(36, 18), nullable=False),
        sa.Column("quote_inventory", sa.Numeric(36, 18), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), index=True),
    )
    op.create_table(
        "ai_decisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("action", sa.String(48), nullable=False, index=True),
        sa.Column("executed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("features_json", sa.JSON(), nullable=False),
        sa.Column("action_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), index=True),
    )
    op.create_table(
        "risk_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("severity", sa.String(16), nullable=False, index=True),
        sa.Column("code", sa.String(64), nullable=False, index=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("bot_id", sa.String(64), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), index=True),
    )
    op.create_table(
        "grid_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bot_id", sa.String(64), nullable=False, index=True),
        sa.Column("mark", sa.Numeric(36, 18), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), index=True),
    )


def downgrade() -> None:
    op.drop_table("grid_snapshots")
    op.drop_table("risk_events")
    op.drop_table("ai_decisions")
    op.drop_table("pnl_ticks")
    op.drop_table("fills")
    op.drop_table("orders")
    op.drop_table("bots")
