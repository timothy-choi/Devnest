"""Add policy and quota tables for Policy & Quota Enforcement.

Converted from: backend/migrations/manual/004_policy_quota.sql

Revision ID: 0005
Revises: 0004
Create Date: 2025-01-01 00:04:00.000000
"""

from __future__ import annotations

from alembic import op

revision: str = "0005"
down_revision: str = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── policy ────────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS policy (
            policy_id   SERIAL PRIMARY KEY,
            name        VARCHAR(128) NOT NULL UNIQUE,
            description VARCHAR(1024),
            policy_type VARCHAR(32)  NOT NULL,
            scope_type  VARCHAR(32)  NOT NULL,
            scope_id    INTEGER,
            rules_json  JSON         NOT NULL DEFAULT '{}',
            is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ  NOT NULL,
            updated_at  TIMESTAMPTZ  NOT NULL
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_policy_name               ON policy (name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_policy_policy_type        ON policy (policy_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_policy_scope_type         ON policy (scope_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_policy_scope_id           ON policy (scope_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_policy_is_active          ON policy (is_active)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_policy_scope_type_scope_id ON policy (scope_type, scope_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_policy_is_active_scope    ON policy (is_active, scope_type)")

    # ── quota ─────────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS quota (
            quota_id               SERIAL PRIMARY KEY,
            scope_type             VARCHAR(32) NOT NULL,
            scope_id               INTEGER,
            max_workspaces         INTEGER     CHECK (max_workspaces >= 0),
            max_running_workspaces INTEGER     CHECK (max_running_workspaces >= 0),
            max_cpu                FLOAT       CHECK (max_cpu >= 0),
            max_memory_mb          INTEGER     CHECK (max_memory_mb >= 0),
            max_storage_mb         INTEGER     CHECK (max_storage_mb >= 0),
            max_sessions           INTEGER     CHECK (max_sessions >= 0),
            max_snapshots          INTEGER     CHECK (max_snapshots >= 0),
            max_runtime_hours      FLOAT       CHECK (max_runtime_hours >= 0),
            created_at             TIMESTAMPTZ NOT NULL,
            updated_at             TIMESTAMPTZ NOT NULL
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_quota_scope_type          ON quota (scope_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_quota_scope_id            ON quota (scope_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_quota_scope_type_scope_id ON quota (scope_type, scope_id)")


def downgrade() -> None:
    for idx in ("ix_quota_scope_type_scope_id", "ix_quota_scope_id", "ix_quota_scope_type"):
        op.execute(f"DROP INDEX IF EXISTS {idx}")
    op.execute("DROP TABLE IF EXISTS quota")

    for idx in (
        "ix_policy_is_active_scope",
        "ix_policy_scope_type_scope_id",
        "ix_policy_is_active",
        "ix_policy_scope_id",
        "ix_policy_scope_type",
        "ix_policy_policy_type",
        "ix_policy_name",
    ):
        op.execute(f"DROP INDEX IF EXISTS {idx}")
    op.execute("DROP TABLE IF EXISTS policy")
