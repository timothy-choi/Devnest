"""Usage event recording and summary queries."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from app.libs.observability.correlation import get_correlation_id

from .enums import UsageEventType
from .models import WorkspaceUsageRecord

logger = logging.getLogger(__name__)


def record_usage(
    session: Session,
    *,
    workspace_id: int | None = None,
    owner_user_id: int | None = None,
    event_type: str,
    quantity: int = 1,
    node_id: str | None = None,
    job_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> WorkspaceUsageRecord:
    """Append one usage record; flush into the caller's transaction (do not commit)."""
    cid = (correlation_id or get_correlation_id() or "").strip() or None
    row = WorkspaceUsageRecord(
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        event_type=event_type,
        quantity=max(0, int(quantity or 0)),
        node_id=node_id,
        job_id=job_id,
        metadata_json=dict(metadata or {}),
        correlation_id=cid[:64] if cid else None,
    )
    session.add(row)
    session.flush()
    return row


@dataclass
class WorkspaceUsageSummary:
    workspace_id: int
    owner_user_id: int
    totals: dict[str, int] = field(default_factory=dict)


@dataclass
class UserUsageSummary:
    owner_user_id: int
    totals_by_event: dict[str, int] = field(default_factory=dict)


def get_workspace_usage_summary(
    session: Session,
    *,
    workspace_id: int,
) -> WorkspaceUsageSummary:
    """Return per-event-type total quantities for a workspace."""
    rows = session.exec(
        select(
            WorkspaceUsageRecord.event_type,
            func.sum(WorkspaceUsageRecord.quantity).label("total"),
        )
        .where(WorkspaceUsageRecord.workspace_id == workspace_id)
        .group_by(WorkspaceUsageRecord.event_type),
    ).all()

    owner_row = session.exec(
        select(WorkspaceUsageRecord.owner_user_id)
        .where(WorkspaceUsageRecord.workspace_id == workspace_id)
        .limit(1),
    ).first()

    return WorkspaceUsageSummary(
        workspace_id=workspace_id,
        owner_user_id=int(owner_row or 0),
        totals={r[0]: int(r[1] or 0) for r in rows},
    )


def get_user_usage_summary(
    session: Session,
    *,
    owner_user_id: int,
) -> UserUsageSummary:
    """Return per-event-type total quantities across all workspaces for a user."""
    rows = session.exec(
        select(
            WorkspaceUsageRecord.event_type,
            func.sum(WorkspaceUsageRecord.quantity).label("total"),
        )
        .where(WorkspaceUsageRecord.owner_user_id == owner_user_id)
        .group_by(WorkspaceUsageRecord.event_type),
    ).all()
    return UserUsageSummary(
        owner_user_id=owner_user_id,
        totals_by_event={r[0]: int(r[1] or 0) for r in rows},
    )
