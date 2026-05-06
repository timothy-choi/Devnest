"""Workspace runtime: applied container quotas and security snapshot.

Revision ID: 0015_ws_runtime_quotas
Revises: 0014_exec_node_host_res
Create Date: 2026-05-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0015_ws_runtime_quotas"
down_revision = "0014_exec_node_host_res"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("workspace_runtime")}
    adds = [
        ("applied_cpu_limit_cores", sa.Float(), True),
        ("applied_memory_limit_mb", sa.Integer(), True),
        ("applied_pids_limit", sa.Integer(), True),
        ("applied_security_options", sa.JSON(), True),
    ]
    for name, col_type, nullable in adds:
        if name not in cols:
            op.add_column("workspace_runtime", sa.Column(name, col_type, nullable=nullable))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("workspace_runtime")}
    for name in (
        "applied_security_options",
        "applied_pids_limit",
        "applied_memory_limit_mb",
        "applied_cpu_limit_cores",
    ):
        if name in cols:
            op.drop_column("workspace_runtime", name)
