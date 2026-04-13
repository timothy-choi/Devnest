"""Initial baseline schema.

Creates all base tables using SQLModel.metadata.create_all() so the migration
always matches the live model definitions and never drifts from hand-written DDL.

EXISTING DEPLOYMENTS (bootstrapped with create_all() at startup):
    alembic stamp 0001
    alembic upgrade head    # runs 0002–latest; all use IF NOT EXISTS so they are no-ops

FRESH DEPLOYMENTS:
    alembic upgrade head    # this revision + subsequent ones

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
from sqlmodel import SQLModel

revision: str = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Import all models to register every table in SQLModel.metadata.
    # The side-effect import in database.py covers all services.
    import app.libs.db.database as _db  # noqa: F401, PLC0415

    bind = op.get_bind()

    # create_all with checkfirst=True is idempotent — safe to run against a
    # database that already has some or all tables (e.g. from a prior create_all
    # call).  Subsequent revisions use IF NOT EXISTS / conditional DDL for the
    # same reason.
    SQLModel.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    # Import so every table is registered before drop_all.
    import app.libs.db.database as _db  # noqa: F401, PLC0415

    bind = op.get_bind()
    SQLModel.metadata.drop_all(bind=bind)
