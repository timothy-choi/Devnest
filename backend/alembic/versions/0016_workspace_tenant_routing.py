"""Tenant subdomain routing: UserAuth.route_subdomain_slug, Workspace.url_slug + gateway_path_prefix.

Revision ID: 0016_tenant_workspace_routing
Revises: 0015_ws_runtime_quotas
Create Date: 2026-05-05
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

revision = "0016_tenant_workspace_routing"
down_revision = "0015_ws_runtime_quotas"
branch_labels = None
depends_on = None


def _slugify(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return (s[:128] or "workspace")


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # --- user_auth.route_subdomain_slug ---
    ua_cols = {c["name"] for c in insp.get_columns("user_auth")}
    if "route_subdomain_slug" not in ua_cols:
        op.add_column(
            "user_auth",
            sa.Column("route_subdomain_slug", sa.String(length=128), nullable=True),
        )
        op.create_index(
            "ix_user_auth_route_subdomain_slug",
            "user_auth",
            ["route_subdomain_slug"],
            unique=True,
        )

    # --- workspace.url_slug, gateway_path_prefix ---
    ws_cols = {c["name"] for c in insp.get_columns("workspace")}
    if "url_slug" not in ws_cols:
        op.add_column(
            "workspace",
            sa.Column("url_slug", sa.String(length=128), nullable=False, server_default=""),
        )
    if "gateway_path_prefix" not in ws_cols:
        op.add_column(
            "workspace",
            sa.Column("gateway_path_prefix", sa.String(length=512), nullable=True),
        )

    # Data backfill on the same connection as Alembic — do **not** call Session.commit() mid-migration
    # (that commits/splits the migration transaction and can leave PostgreSQL in a bad state).
    conn = bind
    rows = conn.execute(sa.text("SELECT user_auth_id, username FROM user_auth")).all()
    used: set[str] = set()
    for uid, username in rows:
        base = _slugify(str(username))
        cand = base or "user"
        n = 2
        while cand in used:
            cand = f"{base}-{n}"
            n += 1
        used.add(cand)
        conn.execute(
            sa.text("UPDATE user_auth SET route_subdomain_slug = :slug WHERE user_auth_id = :uid"),
            {"slug": cand, "uid": uid},
        )

    wrows = conn.execute(
        sa.text("SELECT workspace_id, owner_user_id, name, status FROM workspace WHERE status != 'DELETED'")
    ).all()
    per_owner: dict[int, set[str]] = {}
    for wid, owner_id, name, _st in wrows:
        oid = int(owner_id)
        taken = per_owner.setdefault(oid, set())
        base = _slugify(str(name))
        cand = base or "workspace"
        n = 2
        while cand in taken:
            cand = f"{base}-{n}"
            n += 1
        taken.add(cand)
        conn.execute(
            sa.text("UPDATE workspace SET url_slug = :slug WHERE workspace_id = :wid"),
            {"slug": cand, "wid": wid},
        )

    # Avoid swallowing errors after a failed DDL (would leave PG txn aborted). Use IF NOT EXISTS.
    dialect = bind.dialect.name
    if dialect == "postgresql":
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_workspace_url_slug ON workspace (url_slug)"))
        conn.execute(sa.text("ALTER TABLE workspace ALTER COLUMN url_slug DROP DEFAULT"))
    else:
        insp2 = sa.inspect(bind)
        ix_workspace = insp2.get_indexes("workspace")
        if not any(ix.get("name") == "ix_workspace_url_slug" for ix in ix_workspace):
            op.create_index("ix_workspace_url_slug", "workspace", ["url_slug"])
        try:
            op.alter_column("workspace", "url_slug", server_default=None)
        except Exception:
            pass


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("DROP INDEX IF EXISTS ix_workspace_url_slug"))
    else:
        try:
            op.drop_index("ix_workspace_url_slug", table_name="workspace")
        except Exception:
            pass
    ws_cols = {c["name"] for c in insp.get_columns("workspace")}
    if "gateway_path_prefix" in ws_cols:
        op.drop_column("workspace", "gateway_path_prefix")
    if "url_slug" in ws_cols:
        op.drop_column("workspace", "url_slug")

    ua_cols = {c["name"] for c in insp.get_columns("user_auth")}
    if "route_subdomain_slug" in ua_cols:
        try:
            op.drop_index("ix_user_auth_route_subdomain_slug", table_name="user_auth")
        except Exception:
            pass
        op.drop_column("user_auth", "route_subdomain_slug")
