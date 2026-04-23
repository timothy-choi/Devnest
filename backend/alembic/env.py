"""Alembic environment configuration for DevNest backend.

Supports both offline (SQL generation) and online (live DB) migration modes.

DATABASE URL — same source as the running API: ``get_settings().database_url`` (see
``app.libs.common.config`` module docstring for precedence: ``DEVNEST_DATABASE_URL``,
``DATABASE_URL``, then ``backend/.env`` fallbacks, then component env).

Do not rely on reading ``DATABASE_URL`` alone here; that previously diverged from Settings when
``backend/.env`` contained a different ``DEVNEST_DATABASE_URL``.

``alembic.ini`` sqlalchemy.url is intentionally blank; env.py always supplies the URL.
"""

from __future__ import annotations

import logging
from logging.config import fileConfig

from sqlalchemy import pool
from alembic import context
from sqlmodel import SQLModel

# ── Alembic Config object ────────────────────────────────────────────────────
config = context.config

# Set up Python logging from alembic.ini [loggers] section.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Register all SQLModel models ─────────────────────────────────────────────
# Side-effect import: runs all model-level imports in database.py so every
# SQLModel Table is registered with SQLModel.metadata before autogenerate runs.
import app.libs.db.database as _db_module  # noqa: F401, E402

target_metadata = SQLModel.metadata

_log = logging.getLogger("alembic.env")


# ── Database URL ─────────────────────────────────────────────────────────────


def _get_url() -> str:
    """Resolve the database URL at migration time (must match FastAPI ``get_settings()``)."""
    from app.libs.common.config import format_database_url_for_log, get_settings  # noqa: PLC0415

    url = get_settings().database_url
    _log.info("Alembic effective DB target: %s", format_database_url_for_log(url))
    return url


# ── Offline mode (generate SQL without connecting) ───────────────────────────


def run_migrations_offline() -> None:
    """Emit migration SQL to stdout without a live DB connection.

    Useful for generating SQL to review or apply manually.
    """
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online mode (apply migrations against a live DB) ─────────────────────────


def run_migrations_online() -> None:
    """Apply migrations against the live database."""
    from sqlalchemy import create_engine  # noqa: PLC0415

    connectable = create_engine(
        _get_url(),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


# ── Entry point ───────────────────────────────────────────────────────────────

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
