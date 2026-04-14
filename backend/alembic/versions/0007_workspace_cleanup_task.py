"""Durable workspace_cleanup_task queue for rollback/stop cleanup debt.

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-13 00:07:00.000000
"""

from __future__ import annotations

from alembic import op

revision: str = "0007"
down_revision: str = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS workspace_cleanup_task (
            cleanup_task_id SERIAL PRIMARY KEY,
            workspace_id INTEGER NOT NULL REFERENCES workspace(workspace_id) ON DELETE CASCADE,
            scope VARCHAR(64) NOT NULL,
            detail VARCHAR(8192),
            status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_workspace_cleanup_workspace_scope UNIQUE (workspace_id, scope)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workspace_cleanup_task_workspace_status "
        "ON workspace_cleanup_task (workspace_id, status)",
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_workspace_cleanup_task_workspace_status")
    op.execute("DROP TABLE IF EXISTS workspace_cleanup_task")
