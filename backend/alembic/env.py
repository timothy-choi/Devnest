"""Alembic environment configuration for DevNest backend.

Supports both offline (SQL generation) and online (live DB) migration modes.

DATABASE URL resolution order:
1. DATABASE_URL environment variable (set by CI, Docker, or production env files)
2. app.libs.common.config.get_settings().database_url (reads .env if present)
3. alembic.ini sqlalchemy.url (left blank; env.py always wins)

Importing app.libs.db.database triggers all SQLModel model imports, registering
every table in SQLModel.metadata — which is used as Alembic's target_metadata for
autogenerate support.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlalchemy import text
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


# ── Database URL ─────────────────────────────────────────────────────────────

def _get_url() -> str:
    """Resolve the database URL at migration time."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    # Fall back to application settings (reads .env if present).
    from app.libs.common.config import get_settings  # noqa: PLC0415

    return get_settings().database_url


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
