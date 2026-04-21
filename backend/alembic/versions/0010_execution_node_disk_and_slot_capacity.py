"""execution node disk and slot capacity

Revision ID: 0010_execution_node_disk_and_slot_capacity
Revises: 0009_workspace_secret_store
Create Date: 2026-04-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_execution_node_disk_and_slot_capacity"
down_revision = "0009"
branch_labels = None
depends_on = None


DEFAULT_EXECUTION_NODE_MAX_WORKSPACES = 32
DEFAULT_EXECUTION_NODE_ALLOCATABLE_DISK_MB = 102_400
DEFAULT_WORKSPACE_REQUEST_DISK_MB = 4_096


def upgrade() -> None:
    op.add_column(
        "execution_node",
        sa.Column(
            "max_workspaces",
            sa.Integer(),
            nullable=False,
            server_default=str(DEFAULT_EXECUTION_NODE_MAX_WORKSPACES),
        ),
    )
    op.add_column(
        "execution_node",
        sa.Column(
            "allocatable_disk_mb",
            sa.Integer(),
            nullable=False,
            server_default=str(DEFAULT_EXECUTION_NODE_ALLOCATABLE_DISK_MB),
        ),
    )
    op.create_check_constraint(
        "ck_exec_node_max_workspaces_nonneg",
        "execution_node",
        "max_workspaces >= 0",
    )
    op.create_check_constraint(
        "ck_exec_node_alloc_disk_nonneg",
        "execution_node",
        "allocatable_disk_mb >= 0",
    )
    op.alter_column("execution_node", "max_workspaces", server_default=None)
    op.alter_column("execution_node", "allocatable_disk_mb", server_default=None)

    op.add_column(
        "workspace_runtime",
        sa.Column(
            "reserved_disk_mb",
            sa.Integer(),
            nullable=False,
            server_default=str(DEFAULT_WORKSPACE_REQUEST_DISK_MB),
        ),
    )
    op.execute(
        """
        UPDATE workspace_runtime
        SET reserved_disk_mb = 0
        WHERE COALESCE(node_id, '') = ''
           OR workspace_id IN (
               SELECT workspace_id
               FROM workspace
               WHERE status IN ('STOPPED', 'DELETED', 'ERROR')
           )
        """,
    )
    op.execute(
        """
        UPDATE workspace_runtime
        SET reserved_disk_mb = 4096
        WHERE reserved_disk_mb IS NULL
        """,
    )
    op.create_check_constraint(
        "ck_workspace_runtime_reserved_disk_nonneg",
        "workspace_runtime",
        "reserved_disk_mb >= 0",
    )
    op.alter_column("workspace_runtime", "reserved_disk_mb", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_workspace_runtime_reserved_disk_nonneg", "workspace_runtime", type_="check")
    op.drop_column("workspace_runtime", "reserved_disk_mb")

    op.drop_constraint("ck_exec_node_alloc_disk_nonneg", "execution_node", type_="check")
    op.drop_constraint("ck_exec_node_max_workspaces_nonneg", "execution_node", type_="check")
    op.drop_column("execution_node", "allocatable_disk_mb")
    op.drop_column("execution_node", "max_workspaces")
