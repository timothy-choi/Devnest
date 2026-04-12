"""Quota enforcement logic.

Each ``check_*`` function:
  1. Looks up the most specific applicable Quota row (WORKSPACE > USER > GLOBAL).
  2. If a limit is set and current usage meets or exceeds it, records a QUOTA_EXCEEDED
     audit row (committed durably), then raises ``QuotaExceededError``.
  3. Returns None on success.

Current-usage queries hit the primary tables (Workspace, WorkspaceSnapshot, etc.)
directly rather than the append-only usage ledger — this gives accurate real-time
counts while the usage ledger serves aggregation and historical analysis.

TODO: add max_runtime_hours enforcement via DailyUsageAggregate when rollups exist.
TODO: wire max_cpu / max_memory_mb checks when placement reservations are enforced.
"""

from __future__ import annotations

import logging

from sqlalchemy import func
from sqlmodel import Session, select

from app.libs.observability.correlation import get_correlation_id
from app.services.audit_service.enums import AuditAction, AuditActorType, AuditOutcome
from app.services.audit_service.service import record_audit
from app.services.workspace_service.models import Workspace, WorkspaceSnapshot
from app.services.workspace_service.models.enums import (
    WorkspaceSnapshotStatus,
    WorkspaceStatus,
)

from .enums import ScopeType
from .errors import QuotaExceededError
from .models import Quota

logger = logging.getLogger(__name__)

# Statuses that count toward "active workspace" for max_workspaces
_ACTIVE_WS_STATUSES = frozenset(
    {
        WorkspaceStatus.CREATING.value,
        WorkspaceStatus.STARTING.value,
        WorkspaceStatus.RUNNING.value,
        WorkspaceStatus.STOPPING.value,
        WorkspaceStatus.STOPPED.value,
        WorkspaceStatus.RESTARTING.value,
        WorkspaceStatus.UPDATING.value,
        WorkspaceStatus.ERROR.value,
    }
)

# Statuses that count toward "running workspace" for max_running_workspaces
_RUNNING_WS_STATUSES = frozenset(
    {
        WorkspaceStatus.RUNNING.value,
        WorkspaceStatus.STARTING.value,
        WorkspaceStatus.RESTARTING.value,
    }
)

# Snapshot statuses that consume quota (FAILED ones are dead weight but don't block new snapshots)
_QUOTA_SNAPSHOT_STATUSES = frozenset(
    {
        WorkspaceSnapshotStatus.CREATING.value,
        WorkspaceSnapshotStatus.AVAILABLE.value,
        WorkspaceSnapshotStatus.RESTORING.value,
    }
)


# ---------------------------------------------------------------------------
# Quota resolution
# ---------------------------------------------------------------------------

def _get_effective_quota(
    session: Session,
    *,
    owner_user_id: int | None = None,
    workspace_id: int | None = None,
) -> Quota | None:
    """Return the most specific Quota applicable to this context.

    Precedence: WORKSPACE > USER > GLOBAL.
    """
    if workspace_id is not None:
        ws_quota = session.exec(
            select(Quota)
            .where(Quota.scope_type == ScopeType.WORKSPACE.value)
            .where(Quota.scope_id == workspace_id)
        ).first()
        if ws_quota:
            return ws_quota

    if owner_user_id is not None:
        user_quota = session.exec(
            select(Quota)
            .where(Quota.scope_type == ScopeType.USER.value)
            .where(Quota.scope_id == owner_user_id)
        ).first()
        if user_quota:
            return user_quota

    return session.exec(
        select(Quota)
        .where(Quota.scope_type == ScopeType.GLOBAL.value)
        .where(Quota.scope_id.is_(None))
    ).first()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _exceed_and_raise(
    session: Session,
    *,
    quota_field: str,
    limit: int,
    current: int,
    scope: str,
    owner_user_id: int | None = None,
    workspace_id: int | None = None,
    correlation_id: str | None = None,
) -> None:
    """Record a QUOTA_EXCEEDED audit row, commit it durably, then raise QuotaExceededError."""
    cid = correlation_id or get_correlation_id()
    try:
        record_audit(
            session,
            action=AuditAction.QUOTA_EXCEEDED.value,
            resource_type="quota",
            actor_user_id=owner_user_id,
            actor_type=AuditActorType.USER.value if owner_user_id else AuditActorType.SYSTEM.value,
            outcome=AuditOutcome.DENIED.value,
            workspace_id=workspace_id,
            correlation_id=cid,
            reason=f"Quota exceeded: {quota_field} limit={limit} current={current} scope={scope}",
            metadata={"quota_field": quota_field, "limit": limit, "current": current, "scope": scope},
        )
        session.commit()
    except Exception:
        logger.warning("quota_exceed_audit_commit_failed", exc_info=True)
        try:
            session.rollback()
        except Exception:
            pass
    raise QuotaExceededError(
        quota_field=quota_field, limit=limit, current=current, scope=scope
    )


# ---------------------------------------------------------------------------
# Quota check entry points
# ---------------------------------------------------------------------------

def check_workspace_quota(
    session: Session,
    *,
    owner_user_id: int,
    correlation_id: str | None = None,
) -> None:
    """Raise QuotaExceededError if the user is at or over their max_workspaces limit."""
    quota = _get_effective_quota(session, owner_user_id=owner_user_id)
    if quota is None or quota.max_workspaces is None:
        return

    current = int(
        session.exec(
            select(func.count())
            .select_from(Workspace)
            .where(Workspace.owner_user_id == owner_user_id)
            .where(Workspace.status.in_(list(_ACTIVE_WS_STATUSES)))
        ).one()
    )
    if current >= quota.max_workspaces:
        _exceed_and_raise(
            session,
            quota_field="max_workspaces",
            limit=quota.max_workspaces,
            current=current,
            scope=f"user:{owner_user_id}",
            owner_user_id=owner_user_id,
            correlation_id=correlation_id,
        )


def check_running_workspace_quota(
    session: Session,
    *,
    owner_user_id: int,
    workspace_id: int | None = None,
    correlation_id: str | None = None,
) -> None:
    """Raise QuotaExceededError if starting another workspace would exceed max_running_workspaces.

    ``workspace_id`` is excluded from the count so that re-checking a workspace that is
    already in a running-adjacent state does not double-count it.
    """
    quota = _get_effective_quota(session, owner_user_id=owner_user_id)
    if quota is None or quota.max_running_workspaces is None:
        return

    stmt = (
        select(func.count())
        .select_from(Workspace)
        .where(Workspace.owner_user_id == owner_user_id)
        .where(Workspace.status.in_(list(_RUNNING_WS_STATUSES)))
    )
    if workspace_id is not None:
        stmt = stmt.where(Workspace.workspace_id != workspace_id)

    current = int(session.exec(stmt).one())
    if current >= quota.max_running_workspaces:
        _exceed_and_raise(
            session,
            quota_field="max_running_workspaces",
            limit=quota.max_running_workspaces,
            current=current,
            scope=f"user:{owner_user_id}",
            owner_user_id=owner_user_id,
            correlation_id=correlation_id,
        )


def check_snapshot_quota(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    correlation_id: str | None = None,
) -> None:
    """Raise QuotaExceededError if the workspace is at or over its max_snapshots limit."""
    quota = _get_effective_quota(
        session, owner_user_id=owner_user_id, workspace_id=workspace_id
    )
    if quota is None or quota.max_snapshots is None:
        return

    current = int(
        session.exec(
            select(func.count())
            .select_from(WorkspaceSnapshot)
            .where(WorkspaceSnapshot.workspace_id == workspace_id)
            .where(WorkspaceSnapshot.status.in_(list(_QUOTA_SNAPSHOT_STATUSES)))
        ).one()
    )
    if current >= quota.max_snapshots:
        _exceed_and_raise(
            session,
            quota_field="max_snapshots",
            limit=quota.max_snapshots,
            current=current,
            scope=f"workspace:{workspace_id}",
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )


def check_session_quota(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    current_session_count: int,
    correlation_id: str | None = None,
) -> None:
    """Raise QuotaExceededError if the workspace is at or over its max_sessions limit.

    ``current_session_count`` is passed by the caller (from ``ws.active_sessions_count``)
    to avoid an extra DB query when the workspace row is already loaded.
    """
    quota = _get_effective_quota(
        session, owner_user_id=owner_user_id, workspace_id=workspace_id
    )
    if quota is None or quota.max_sessions is None:
        return

    if current_session_count >= quota.max_sessions:
        _exceed_and_raise(
            session,
            quota_field="max_sessions",
            limit=quota.max_sessions,
            current=current_session_count,
            scope=f"workspace:{workspace_id}",
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )
