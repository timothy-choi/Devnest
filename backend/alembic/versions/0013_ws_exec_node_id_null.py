"""Allow NULL workspace.execution_node_id until worker placement (async create).

Revision ID: 0013_ws_exec_node_id_null
Revises: 0012_ws_runtime_gateway_rt
Create Date: 2026-05-03
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op


revision = "0013_ws_exec_node_id_null"
down_revision = "0012_ws_runtime_gateway_rt"
branch_labels = None
depends_on = None


def _execution_node_id_nullable(bind: Any) -> bool | None:
    insp = sa.inspect(bind)
    for col in insp.get_columns("workspace"):
        if str(col["name"]) == "execution_node_id":
            return bool(col.get("nullable", True))
    return None


def upgrade() -> None:
    bind = op.get_bind()
    if _execution_node_id_nullable(bind) is False:
        op.alter_column("workspace", "execution_node_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    if _execution_node_id_nullable(bind) is not True:
        return
    # Restore NOT NULL: pin any orphan rows to the smallest execution_node id (bootstrap primary).
    op.execute(
        sa.text(
            """
            UPDATE workspace AS w
            SET execution_node_id = sub.id
            FROM (
                SELECT id FROM execution_node ORDER BY id ASC LIMIT 1
            ) AS sub
            WHERE w.execution_node_id IS NULL
            """,
        ),
    )
    op.alter_column("workspace", "execution_node_id", existing_type=sa.Integer(), nullable=False)
