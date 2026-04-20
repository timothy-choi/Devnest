"""Encrypted workspace secret store for runtime-only injection.

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-19 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision: str = "0009"
down_revision: str = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS workspace_secret (
            workspace_secret_id SERIAL PRIMARY KEY,
            workspace_id INTEGER NOT NULL REFERENCES workspace(workspace_id) ON DELETE CASCADE,
            secret_name VARCHAR(128) NOT NULL,
            encrypted_value VARCHAR(8192) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_workspace_secret_name UNIQUE (workspace_id, secret_name)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workspace_secret_workspace_id "
        "ON workspace_secret (workspace_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workspace_secret_secret_name "
        "ON workspace_secret (secret_name)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_workspace_secret_secret_name")
    op.execute("DROP INDEX IF EXISTS ix_workspace_secret_workspace_id")
    op.execute("DROP TABLE IF EXISTS workspace_secret")
