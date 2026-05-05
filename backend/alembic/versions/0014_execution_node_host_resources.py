"""Execution node host disk/memory telemetry for resource-aware scheduling.

Revision ID: 0014_exec_node_host_res
Revises: 0013_ws_exec_node_id_null
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0014_exec_node_host_res"
down_revision = "0013_ws_exec_node_id_null"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("execution_node")}
    adds = [
        ("disk_total_mb", sa.Integer(), True),
        ("disk_free_mb", sa.Integer(), True),
        ("memory_total_mb", sa.Integer(), True),
        ("memory_free_mb", sa.Integer(), True),
        ("last_resource_check_at", sa.DateTime(timezone=True), True),
        ("resource_status", sa.String(length=32), True),
        ("resource_warning_message", sa.String(length=512), True),
    ]
    for name, col_type, nullable in adds:
        if name not in cols:
            op.add_column("execution_node", sa.Column(name, col_type, nullable=nullable))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("execution_node")}
    for name in (
        "resource_warning_message",
        "resource_status",
        "last_resource_check_at",
        "memory_free_mb",
        "memory_total_mb",
        "disk_free_mb",
        "disk_total_mb",
    ):
        if name in cols:
            op.drop_column("execution_node", name)
