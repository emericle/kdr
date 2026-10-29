"""Ensure volume column and unique bar index on market_data without dropping rows.

Revision ID: 0002_market_data_volume_unique
Revises: 0001_initial_schema
Create Date: 2026-10-15 00:05:00

The identifier stays at or under 32 characters because Alembic stores it in an
``alembic_version`` column declared as ``varchar(32)``; a longer name makes
``upgrade head`` fail with a right-truncation error on every deploy.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_market_data_volume_unique"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ensure_column(
    inspector: sa.Inspector,
    table: str,
    column: sa.Column,
    backfill_sql: str | None = None,
) -> None:
    existing = {col["name"] for col in inspector.get_columns(table)}
    if column.name not in existing:
        op.add_column(table, column)
        if backfill_sql:
            op.execute(sa.text(backfill_sql))


def _ensure_index(
    inspector: sa.Inspector,
    table: str,
    index_name: str,
    columns: list[str],
    unique: bool = False,
) -> None:
    existing = {idx["name"] for idx in inspector.get_indexes(table)}
    if index_name not in existing:
        op.create_index(index_name, table, columns, unique=unique)


def upgrade() -> None:
    """Add missing columns and indexes in place so existing bars survive upgrades."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _ensure_column(
        inspector,
        "market_data",
        sa.Column("volume", sa.Float(), server_default=sa.text("0.0"), nullable=True),
        backfill_sql="UPDATE market_data SET volume = 0.0 WHERE volume IS NULL",
    )
    _ensure_column(inspector, "sentiment_data", sa.Column("source", sa.String(), nullable=True))
    _ensure_column(
        inspector,
        "model_weights",
        sa.Column("last_trained", sa.DateTime(timezone=True), nullable=True),
    )

    _ensure_index(inspector, "market_data", "ix_market_data_symbol", ["symbol"])
    _ensure_index(inspector, "market_data", "ix_market_data_timestamp", ["timestamp"])
    _ensure_index(
        inspector,
        "market_data",
        "market_data_bar_unique",
        ["symbol", "timestamp"],
        unique=True,
    )
    _ensure_index(inspector, "sentiment_data", "ix_sentiment_data_symbol", ["symbol"])


def downgrade() -> None:
    """No-op downgrade: KDR migrations never drop columns or tables holding market data."""
    pass
