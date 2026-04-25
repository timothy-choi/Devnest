"""workspace.execution_node_id — Phase 1 node registry FK

Revision ID: 0011_workspace_execution_node_fk
Revises: 0010
Create Date: 2026-04-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_workspace_execution_node_fk"
down_revision = "0010"
branch_labels = None
depends_on = None


def _column_names(bind: sa.engine.Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    return {str(col["name"]) for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()

    # Fresh ``alembic upgrade`` databases may have no rows yet (bootstrap normally runs in ``init_db``).
    from sqlmodel import Session

    from app.services.placement_service.bootstrap import ensure_default_local_execution_node

    with Session(bind) as session:
        ensure_default_local_execution_node(session)
        session.commit()

    workspace_cols = _column_names(bind, "workspace")

    if "execution_node_id" not in workspace_cols:
        op.add_column(
            "workspace",
            sa.Column("execution_node_id", sa.Integer(), nullable=True),
        )
        op.create_index("ix_workspace_execution_node_id", "workspace", ["execution_node_id"], unique=False)
        op.create_foreign_key(
            "fk_workspace_execution_node_id",
            "workspace",
            "execution_node",
            ["execution_node_id"],
            ["id"],
        )

    # Pin workspaces to the runtime node's registry row when node_key matches.
    op.execute(
        """
        UPDATE workspace AS w
        SET execution_node_id = en.id
        FROM workspace_runtime AS wr
        JOIN execution_node AS en ON en.node_key = wr.node_id
        WHERE w.workspace_id = wr.workspace_id
          AND wr.node_id IS NOT NULL
          AND TRIM(wr.node_id) <> ''
          AND w.execution_node_id IS NULL
        """,
    )

    # Remaining rows (no runtime / unknown node_key): first execution_node by id (bootstrap default).
    op.execute(
        """
        UPDATE workspace
        SET execution_node_id = (SELECT id FROM execution_node ORDER BY id ASC LIMIT 1)
        WHERE execution_node_id IS NULL
          AND EXISTS (SELECT 1 FROM execution_node)
        """,
    )

    # Enforce NOT NULL after backfill (requires at least one execution_node row).
    bind2 = op.get_bind()
    workspace_cols2 = _column_names(bind2, "workspace")
    if "execution_node_id" in workspace_cols2:
        op.alter_column("workspace", "execution_node_id", nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    workspace_cols = _column_names(bind, "workspace")
    if "execution_node_id" not in workspace_cols:
        return
    op.drop_constraint("fk_workspace_execution_node_id", "workspace", type_="foreignkey")
    op.drop_index("ix_workspace_execution_node_id", table_name="workspace")
    op.drop_column("workspace", "execution_node_id")
