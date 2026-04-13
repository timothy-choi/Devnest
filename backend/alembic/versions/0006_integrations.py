"""Add integration tables: user_provider_token, workspace_repository,
workspace_ci_config, ci_trigger_record.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-12 00:06:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── user_provider_token ───────────────────────────────────────────────────
    op.create_table(
        "user_provider_token",
        sa.Column("token_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user_auth.user_auth_id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("access_token_encrypted", sa.String(2048), nullable=False),
        sa.Column("refresh_token_encrypted", sa.String(2048), nullable=True),
        sa.Column("scopes", sa.String(512), nullable=False, server_default=""),
        sa.Column("provider_user_id", sa.String(255), nullable=False),
        sa.Column("provider_username", sa.String(255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_provider_token_user_id", "user_provider_token", ["user_id"])
    op.create_index("ix_user_provider_token_provider", "user_provider_token", ["provider"])
    op.create_unique_constraint(
        "uq_user_provider_token", "user_provider_token", ["user_id", "provider"]
    )

    # ── workspace_repository ──────────────────────────────────────────────────
    op.create_table(
        "workspace_repository",
        sa.Column("repo_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspace.workspace_id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("user_auth.user_auth_id", ondelete="SET NULL"), nullable=False),
        sa.Column("repo_url", sa.String(1024), nullable=False),
        sa.Column("branch", sa.String(255), nullable=False, server_default="main"),
        sa.Column("clone_dir", sa.String(1024), nullable=False),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("provider_repo_name", sa.String(512), nullable=True),
        sa.Column("clone_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_msg", sa.Text, nullable=True),
        sa.Column("last_job_id", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workspace_repository_workspace_id", "workspace_repository", ["workspace_id"])
    op.create_index("ix_workspace_repository_owner_user_id", "workspace_repository", ["owner_user_id"])

    # ── workspace_ci_config ───────────────────────────────────────────────────
    op.create_table(
        "workspace_ci_config",
        sa.Column("ci_config_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspace.workspace_id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("user_auth.user_auth_id", ondelete="SET NULL"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False, server_default="github_actions"),
        sa.Column("repo_owner", sa.String(255), nullable=False),
        sa.Column("repo_name", sa.String(255), nullable=False),
        sa.Column("workflow_file", sa.String(255), nullable=False, server_default="main.yml"),
        sa.Column("default_branch", sa.String(255), nullable=False, server_default="main"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workspace_ci_config_workspace_id", "workspace_ci_config", ["workspace_id"])
    op.create_unique_constraint(
        "uq_workspace_ci_config", "workspace_ci_config", ["workspace_id"]
    )

    # ── ci_trigger_record ─────────────────────────────────────────────────────
    op.create_table(
        "ci_trigger_record",
        sa.Column("trigger_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspace.workspace_id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("user_auth.user_auth_id", ondelete="SET NULL"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False, server_default="github_actions"),
        sa.Column("event_type", sa.String(128), nullable=False, server_default="devnest_trigger"),
        sa.Column("ref", sa.String(255), nullable=True),
        sa.Column("inputs_json", sa.JSON, nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="triggered"),
        sa.Column("error_msg", sa.Text, nullable=True),
        sa.Column("provider_run_url", sa.String(1024), nullable=True),
    )
    op.create_index("ix_ci_trigger_record_workspace_id", "ci_trigger_record", ["workspace_id"])
    op.create_index("ix_ci_trigger_record_owner_user_id", "ci_trigger_record", ["owner_user_id"])


def downgrade() -> None:
    op.drop_table("ci_trigger_record")
    op.drop_table("workspace_ci_config")
    op.drop_table("workspace_repository")
    op.drop_table("user_provider_token")
