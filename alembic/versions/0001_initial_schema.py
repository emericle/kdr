"""Initial core tables: market_data, sentiment_data, model_weights.

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-10-15 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the initial tables when missing, never dropping existing data."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "market_data" not in existing_tables:
        op.create_table(
            "market_data",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("symbol", sa.String(), nullable=True),
            sa.Column("timestamp", sa.DateTime(timezone=True), nullable=True),
            sa.Column("open", sa.Float(), nullable=True),
            sa.Column("high", sa.Float(), nullable=True),
            sa.Column("low", sa.Float(), nullable=True),
            sa.Column("close", sa.Float(), nullable=True),
        )

    if "sentiment_data" not in existing_tables:
        op.create_table(
            "sentiment_data",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("symbol", sa.String(), nullable=True),
            sa.Column("timestamp", sa.DateTime(timezone=True), nullable=True),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("source", sa.String(), nullable=True),
        )

    if "model_weights" not in existing_tables:
        op.create_table(
            "model_weights",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("symbol", sa.String(), nullable=True, unique=True),
            sa.Column("last_trained", sa.DateTime(timezone=True), nullable=True),
            sa.Column("weights_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    """No-op downgrade: KDR migrations never drop market data tables."""
    pass
