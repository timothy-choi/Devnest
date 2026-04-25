"""workspace.execution_node_id — Phase 1 node registry FK

Revision ID: 0011_workspace_execution_node_fk
Revises: 0010_exec_node_capacity
Create Date: 2026-04-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_workspace_execution_node_fk"
down_revision = "0010_exec_node_capacity"
branch_labels = None
depends_on = None


def _column_names(bind: sa.engine.Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    return {str(col["name"]) for col in inspector.get_columns(table_name)}


def _workspace_execution_node_id_nullable(bind: sa.engine.Connection) -> bool:
    inspector = sa.inspect(bind)
    for col in inspector.get_columns("workspace"):
        if str(col["name"]) == "execution_node_id":
            return bool(col.get("nullable", True))
    return True


def upgrade() -> None:
    bind = op.get_bind()

    # Fresh ``alembic upgrade`` databases may have no rows yet (bootstrap normally runs in ``init_db``).
    from sqlmodel import Session

    from app.services.placement_service.bootstrap import ensure_default_local_execution_node

    with Session(bind) as session:
        default_node = ensure_default_local_execution_node(session)
        default_node_id = default_node.id
        session.commit()
    if default_node_id is None:
        raise RuntimeError("ensure_default_local_execution_node returned a row without id")

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

    # Remaining rows (no runtime / unknown node_key): same default as bootstrap (not arbitrary MIN(id)).
    op.execute(
        sa.text("UPDATE workspace SET execution_node_id = :nid WHERE execution_node_id IS NULL").bindparams(
            nid=int(default_node_id),
        ),
    )

    # Enforce NOT NULL after backfill (idempotent if a previous partial run already set NOT NULL).
    bind2 = op.get_bind()
    workspace_cols2 = _column_names(bind2, "workspace")
    if "execution_node_id" in workspace_cols2 and _workspace_execution_node_id_nullable(bind2):
        op.alter_column("workspace", "execution_node_id", nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    workspace_cols = _column_names(bind, "workspace")
    if "execution_node_id" not in workspace_cols:
        return
    op.drop_constraint("fk_workspace_execution_node_id", "workspace", type_="foreignkey")
    op.drop_index("ix_workspace_execution_node_id", table_name="workspace")
    op.drop_column("workspace", "execution_node_id")
