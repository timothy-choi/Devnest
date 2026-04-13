"""Add audit_log and workspace_usage_record tables.

Converted from: backend/migrations/manual/002_audit_usage.sql

Revision ID: 0003
Revises: 0002
Create Date: 2025-01-01 00:02:00.000000
"""

from __future__ import annotations

from alembic import op

revision: str = "0003"
down_revision: str = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── audit_log ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            audit_log_id   SERIAL PRIMARY KEY,
            actor_user_id  INTEGER      REFERENCES user_auth(user_auth_id),
            actor_type     VARCHAR(32)  NOT NULL,
            action         VARCHAR(128) NOT NULL,
            resource_type  VARCHAR(64)  NOT NULL,
            resource_id    VARCHAR(255),
            workspace_id   INTEGER      REFERENCES workspace(workspace_id),
            job_id         INTEGER      REFERENCES workspace_job(workspace_job_id),
            node_id        VARCHAR(255),
            outcome        VARCHAR(32)  NOT NULL,
            reason         VARCHAR(4096),
            metadata_json  JSONB,
            correlation_id VARCHAR(64),
            created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_actor_user_id              ON audit_log (actor_user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_action                     ON audit_log (action)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_resource_type              ON audit_log (resource_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_workspace_id               ON audit_log (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_job_id                     ON audit_log (job_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_outcome                    ON audit_log (outcome)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_correlation_id             ON audit_log (correlation_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_workspace_id_created_at    ON audit_log (workspace_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_actor_user_id_created_at   ON audit_log (actor_user_id, created_at)")

    # ── workspace_usage_record ────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workspace_usage_record (
            usage_record_id SERIAL PRIMARY KEY,
            workspace_id    INTEGER      REFERENCES workspace(workspace_id),
            owner_user_id   INTEGER      REFERENCES user_auth(user_auth_id),
            event_type      VARCHAR(64)  NOT NULL,
            quantity        INTEGER      NOT NULL DEFAULT 1,
            node_id         VARCHAR(255),
            job_id          INTEGER      REFERENCES workspace_job(workspace_job_id),
            metadata_json   JSONB,
            correlation_id  VARCHAR(64),
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_usage_workspace_id           ON workspace_usage_record (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_usage_owner_user_id          ON workspace_usage_record (owner_user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_usage_event_type             ON workspace_usage_record (event_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_usage_job_id                 ON workspace_usage_record (job_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_usage_workspace_event_created ON workspace_usage_record (workspace_id, event_type, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_usage_owner_event_created    ON workspace_usage_record (owner_user_id, event_type, created_at)")


def downgrade() -> None:
    for idx in (
        "ix_usage_owner_event_created",
        "ix_usage_workspace_event_created",
        "ix_usage_job_id",
        "ix_usage_event_type",
        "ix_usage_owner_user_id",
        "ix_usage_workspace_id",
    ):
        op.execute(f"DROP INDEX IF EXISTS {idx}")
    op.execute("DROP TABLE IF EXISTS workspace_usage_record")

    for idx in (
        "ix_audit_log_actor_user_id_created_at",
        "ix_audit_log_workspace_id_created_at",
        "ix_audit_log_correlation_id",
        "ix_audit_log_outcome",
        "ix_audit_log_job_id",
        "ix_audit_log_workspace_id",
        "ix_audit_log_resource_type",
        "ix_audit_log_action",
        "ix_audit_log_actor_user_id",
    ):
        op.execute(f"DROP INDEX IF EXISTS {idx}")
    op.execute("DROP TABLE IF EXISTS audit_log")
