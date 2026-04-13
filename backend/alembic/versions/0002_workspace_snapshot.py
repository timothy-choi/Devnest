"""Add workspace_snapshot table and workspace_job.workspace_snapshot_id column.

Converted from: backend/migrations/manual/001_workspace_snapshot.sql

Revision ID: 0002
Revises: 0001
Create Date: 2025-01-01 00:01:00.000000
"""

from __future__ import annotations

from alembic import op

revision: str = "0002"
down_revision: str = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS workspace_snapshot (
            workspace_snapshot_id SERIAL PRIMARY KEY,
            workspace_id          INTEGER      NOT NULL REFERENCES workspace(workspace_id),
            name                  VARCHAR(255) NOT NULL,
            description           VARCHAR(8192),
            storage_uri           VARCHAR(1024) NOT NULL,
            status                VARCHAR(32)   NOT NULL,
            size_bytes            INTEGER,
            created_by_user_id    INTEGER       NOT NULL REFERENCES user_auth(user_auth_id),
            created_at            TIMESTAMPTZ   NOT NULL,
            metadata_json         JSON
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_snapshot_workspace_id       ON workspace_snapshot (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_snapshot_name               ON workspace_snapshot (name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_snapshot_status             ON workspace_snapshot (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_snapshot_created_by_user_id ON workspace_snapshot (created_by_user_id)")

    op.execute("""
        ALTER TABLE workspace_job
            ADD COLUMN IF NOT EXISTS workspace_snapshot_id INTEGER
                REFERENCES workspace_snapshot(workspace_snapshot_id)
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_job_workspace_snapshot_id ON workspace_job (workspace_snapshot_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_workspace_job_workspace_snapshot_id")
    op.execute("ALTER TABLE workspace_job DROP COLUMN IF EXISTS workspace_snapshot_id")

    op.execute("DROP INDEX IF EXISTS ix_workspace_snapshot_created_by_user_id")
    op.execute("DROP INDEX IF EXISTS ix_workspace_snapshot_status")
    op.execute("DROP INDEX IF EXISTS ix_workspace_snapshot_name")
    op.execute("DROP INDEX IF EXISTS ix_workspace_snapshot_workspace_id")
    op.execute("DROP TABLE IF EXISTS workspace_snapshot")
