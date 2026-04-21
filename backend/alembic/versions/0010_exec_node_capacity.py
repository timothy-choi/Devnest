"""execution node disk and slot capacity

Revision ID: 0010_exec_node_capacity
Revises: 0009
Create Date: 2026-04-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_exec_node_capacity"
down_revision = "0009"
branch_labels = None
depends_on = None


DEFAULT_EXECUTION_NODE_MAX_WORKSPACES = 32
DEFAULT_EXECUTION_NODE_ALLOCATABLE_DISK_MB = 102_400
DEFAULT_WORKSPACE_REQUEST_DISK_MB = 4_096


def _column_names(bind: sa.engine.Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    return {str(col["name"]) for col in inspector.get_columns(table_name)}


def _check_constraint_names(bind: sa.engine.Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    return {str(ck["name"]) for ck in inspector.get_check_constraints(table_name) if ck.get("name")}


def upgrade() -> None:
    bind = op.get_bind()

    execution_node_columns = _column_names(bind, "execution_node")
    execution_node_checks = _check_constraint_names(bind, "execution_node")
    workspace_runtime_columns = _column_names(bind, "workspace_runtime")
    workspace_runtime_checks = _check_constraint_names(bind, "workspace_runtime")

    if "max_workspaces" not in execution_node_columns:
        op.add_column(
            "execution_node",
            sa.Column(
                "max_workspaces",
                sa.Integer(),
                nullable=False,
                server_default=str(DEFAULT_EXECUTION_NODE_MAX_WORKSPACES),
            ),
        )
        op.alter_column("execution_node", "max_workspaces", server_default=None)

    if "allocatable_disk_mb" not in execution_node_columns:
        op.add_column(
            "execution_node",
            sa.Column(
                "allocatable_disk_mb",
                sa.Integer(),
                nullable=False,
                server_default=str(DEFAULT_EXECUTION_NODE_ALLOCATABLE_DISK_MB),
            ),
        )
        op.alter_column("execution_node", "allocatable_disk_mb", server_default=None)

    if "ck_exec_node_max_workspaces_nonneg" not in execution_node_checks:
        op.create_check_constraint(
            "ck_exec_node_max_workspaces_nonneg",
            "execution_node",
            "max_workspaces >= 0",
        )

    if "ck_exec_node_alloc_disk_nonneg" not in execution_node_checks:
        op.create_check_constraint(
            "ck_exec_node_alloc_disk_nonneg",
            "execution_node",
            "allocatable_disk_mb >= 0",
        )

    if "reserved_disk_mb" not in workspace_runtime_columns:
        op.add_column(
            "workspace_runtime",
            sa.Column(
                "reserved_disk_mb",
                sa.Integer(),
                nullable=False,
                server_default=str(DEFAULT_WORKSPACE_REQUEST_DISK_MB),
            ),
        )
        op.alter_column("workspace_runtime", "reserved_disk_mb", server_default=None)

    op.execute(
        f"""
        UPDATE workspace_runtime
        SET reserved_disk_mb = {DEFAULT_WORKSPACE_REQUEST_DISK_MB}
        WHERE reserved_disk_mb IS NULL
        """,
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

    if "ck_workspace_runtime_reserved_disk_nonneg" not in workspace_runtime_checks:
        op.create_check_constraint(
            "ck_workspace_runtime_reserved_disk_nonneg",
            "workspace_runtime",
            "reserved_disk_mb >= 0",
        )


def downgrade() -> None:
    bind = op.get_bind()
    execution_node_columns = _column_names(bind, "execution_node")
    execution_node_checks = _check_constraint_names(bind, "execution_node")
    workspace_runtime_columns = _column_names(bind, "workspace_runtime")
    workspace_runtime_checks = _check_constraint_names(bind, "workspace_runtime")

    if "ck_workspace_runtime_reserved_disk_nonneg" in workspace_runtime_checks:
        op.drop_constraint("ck_workspace_runtime_reserved_disk_nonneg", "workspace_runtime", type_="check")
    if "reserved_disk_mb" in workspace_runtime_columns:
        op.drop_column("workspace_runtime", "reserved_disk_mb")

    if "ck_exec_node_alloc_disk_nonneg" in execution_node_checks:
        op.drop_constraint("ck_exec_node_alloc_disk_nonneg", "execution_node", type_="check")
    if "ck_exec_node_max_workspaces_nonneg" in execution_node_checks:
        op.drop_constraint("ck_exec_node_max_workspaces_nonneg", "execution_node", type_="check")
    if "allocatable_disk_mb" in execution_node_columns:
        op.drop_column("execution_node", "allocatable_disk_mb")
    if "max_workspaces" in execution_node_columns:
        op.drop_column("execution_node", "max_workspaces")
