"""workspace_runtime.gateway_route_target — Traefik upstream (Phase 3b Step 9)

Revision ID: 0012_ws_runtime_gateway_rt
Revises: 0011_workspace_execution_node_fk
Create Date: 2026-04-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_ws_runtime_gateway_rt"
down_revision = "0011_workspace_execution_node_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("workspace_runtime")}
    if "gateway_route_target" not in cols:
        op.add_column(
            "workspace_runtime",
            sa.Column("gateway_route_target", sa.String(length=512), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("workspace_runtime")}
    if "gateway_route_target" in cols:
        op.drop_column("workspace_runtime", "gateway_route_target")
