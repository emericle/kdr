"""Alembic migration environment bound to ``src.database.Base.metadata``.

Database URL resolution delegates to :func:`src.database.get_database_url` when
no explicit ``sqlalchemy.url`` or ``connection`` was handed to the Alembic
``Config`` object, so CLI runs and programmatic test runs share the same source
of truth without hardcoding credentials in ``alembic.ini``.
"""
from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from src.database import Base, get_database_url

config = context.config
target_metadata = Base.metadata


def _resolve_url() -> str:
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    url = get_database_url()
    if not url:
        raise RuntimeError(
            "No database URL configured for Alembic. Set DATABASE_URL in .env "
            "(or QA_DATABASE_URL with USE_QA_DB=1)."
        )
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = _resolve_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection: object) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)  # type: ignore[arg-type]
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = config.attributes.get("connection")
    if connectable is not None:
        _run_with_connection(connectable)
        return

    url = _resolve_url()
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = url
    connect_args = {"connect_timeout": 5} if url.startswith("postgresql") else {}
    engine = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    with engine.connect() as connection:
        _run_with_connection(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
