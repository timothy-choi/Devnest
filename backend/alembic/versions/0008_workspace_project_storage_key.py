"""Persist per-workspace project storage keys for host bind-mount isolation.

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-17 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision: str = "0008"
down_revision: str = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE workspace "
        "ADD COLUMN IF NOT EXISTS project_storage_key VARCHAR(64)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workspace_project_storage_key "
        "ON workspace (project_storage_key)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_workspace_project_storage_key")
    op.execute("ALTER TABLE workspace DROP COLUMN IF EXISTS project_storage_key")
