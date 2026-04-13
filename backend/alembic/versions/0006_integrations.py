"""Add integration tables: user_provider_token, workspace_repository,
workspace_ci_config, ci_trigger_record.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-12 00:06:00.000000
"""

from __future__ import annotations

from alembic import op

revision: str = "0006"
down_revision: str = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # All DDL uses IF NOT EXISTS / IF EXISTS so this migration is idempotent.
    # This is required because migration 0001 calls SQLModel.metadata.create_all()
    # which creates every currently-registered model in one shot — meaning these
    # tables may already exist when this revision runs for the first time.

    # ── user_provider_token ───────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_provider_token (
            token_id    SERIAL PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES user_auth(user_auth_id) ON DELETE CASCADE,
            provider    VARCHAR(32)  NOT NULL,
            access_token_encrypted  VARCHAR(2048) NOT NULL,
            refresh_token_encrypted VARCHAR(2048),
            scopes          VARCHAR(512) NOT NULL DEFAULT '',
            provider_user_id VARCHAR(255) NOT NULL,
            provider_username VARCHAR(255),
            expires_at  TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL,
            updated_at  TIMESTAMPTZ NOT NULL,
            CONSTRAINT uq_user_provider_token UNIQUE (user_id, provider)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_provider_token_user_id ON user_provider_token (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_provider_token_provider ON user_provider_token (provider)")

    # ── workspace_repository ──────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workspace_repository (
            repo_id     SERIAL PRIMARY KEY,
            workspace_id INTEGER NOT NULL REFERENCES workspace(workspace_id) ON DELETE CASCADE,
            owner_user_id INTEGER NOT NULL REFERENCES user_auth(user_auth_id) ON DELETE SET NULL,
            repo_url    VARCHAR(1024) NOT NULL,
            branch      VARCHAR(255) NOT NULL DEFAULT 'main',
            clone_dir   VARCHAR(1024) NOT NULL,
            provider    VARCHAR(32),
            provider_repo_name VARCHAR(512),
            clone_status VARCHAR(32) NOT NULL DEFAULT 'pending',
            last_synced_at TIMESTAMPTZ,
            error_msg   TEXT,
            last_job_id INTEGER,
            created_at  TIMESTAMPTZ NOT NULL,
            updated_at  TIMESTAMPTZ NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_repository_workspace_id  ON workspace_repository (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_repository_owner_user_id ON workspace_repository (owner_user_id)")

    # ── workspace_ci_config ───────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workspace_ci_config (
            ci_config_id SERIAL PRIMARY KEY,
            workspace_id INTEGER NOT NULL REFERENCES workspace(workspace_id) ON DELETE CASCADE,
            owner_user_id INTEGER NOT NULL REFERENCES user_auth(user_auth_id) ON DELETE SET NULL,
            provider        VARCHAR(64)  NOT NULL DEFAULT 'github_actions',
            repo_owner      VARCHAR(255) NOT NULL,
            repo_name       VARCHAR(255) NOT NULL,
            workflow_file   VARCHAR(255) NOT NULL DEFAULT 'main.yml',
            default_branch  VARCHAR(255) NOT NULL DEFAULT 'main',
            is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL,
            updated_at  TIMESTAMPTZ NOT NULL,
            CONSTRAINT uq_workspace_ci_config UNIQUE (workspace_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_ci_config_workspace_id ON workspace_ci_config (workspace_id)")

    # ── ci_trigger_record ─────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS ci_trigger_record (
            trigger_id   SERIAL PRIMARY KEY,
            workspace_id INTEGER NOT NULL REFERENCES workspace(workspace_id) ON DELETE CASCADE,
            owner_user_id INTEGER NOT NULL REFERENCES user_auth(user_auth_id) ON DELETE SET NULL,
            provider     VARCHAR(64)  NOT NULL DEFAULT 'github_actions',
            event_type   VARCHAR(128) NOT NULL DEFAULT 'devnest_trigger',
            ref          VARCHAR(255),
            inputs_json  JSONB,
            triggered_at TIMESTAMPTZ NOT NULL,
            status       VARCHAR(32)  NOT NULL DEFAULT 'triggered',
            error_msg    TEXT,
            provider_run_url VARCHAR(1024)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_ci_trigger_record_workspace_id  ON ci_trigger_record (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ci_trigger_record_owner_user_id ON ci_trigger_record (owner_user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ci_trigger_record")
    op.execute("DROP TABLE IF EXISTS workspace_ci_config")
    op.execute("DROP TABLE IF EXISTS workspace_repository")
    op.execute("DROP TABLE IF EXISTS user_provider_token")
