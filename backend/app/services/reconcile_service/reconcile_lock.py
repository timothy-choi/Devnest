"""PostgreSQL session advisory locks for reconcile (duplicate worker suppression).

Uses ``pg_try_advisory_lock`` / ``pg_advisory_unlock`` (session-scoped) so locks survive
inner commits from ``DbTopologyAdapter`` during the same DB connection. On SQLite and other
dialects, locking is a no-op (single-writer semantics).
"""

from __future__ import annotations

import logging

from sqlalchemy import text as sa_text
from sqlmodel import Session

logger = logging.getLogger(__name__)

# Arbitrary stable key class for DevNest reconcile (avoid collisions with other app locks).
_RECONCILE_ADVISORY_KEY1 = 881_002_003


def try_acquire_workspace_reconcile_lock(session: Session, workspace_id: int) -> bool:
    """Try to acquire a session-level advisory lock for this workspace. Non-blocking."""
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return True
    k2 = int(workspace_id) & 0x7FFF_FFFF
    try:
        row = session.execute(
            sa_text("SELECT pg_try_advisory_lock(:k1, :k2)"),
            {"k1": _RECONCILE_ADVISORY_KEY1, "k2": k2},
        ).scalar_one()
        return bool(row)
    except Exception:
        logger.warning(
            "reconcile_advisory_lock_acquire_failed",
            extra={"workspace_id": workspace_id},
            exc_info=True,
        )
        return False


def release_workspace_reconcile_lock(session: Session, workspace_id: int) -> None:
    """Release session advisory lock if held (idempotent on PostgreSQL)."""
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    k2 = int(workspace_id) & 0x7FFF_FFFF
    try:
        session.execute(
            sa_text("SELECT pg_advisory_unlock(:k1, :k2)"),
            {"k1": _RECONCILE_ADVISORY_KEY1, "k2": k2},
        )
    except Exception:
        logger.warning(
            "reconcile_advisory_lock_release_failed",
            extra={"workspace_id": workspace_id},
            exc_info=True,
        )
