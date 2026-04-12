"""Append-only audit record helpers."""

from __future__ import annotations

import logging
from typing import Any

from sqlmodel import Session, select

from app.libs.observability.correlation import get_correlation_id
from app.libs.observability.log_events import LogEvent, log_event

from .enums import AuditActorType, AuditOutcome
from .models import AuditLog

logger = logging.getLogger(__name__)


def record_audit(
    session: Session,
    *,
    action: str,
    resource_type: str,
    outcome: str = AuditOutcome.SUCCESS.value,
    actor_user_id: int | None = None,
    actor_type: str = AuditActorType.USER.value,
    resource_id: str | int | None = None,
    workspace_id: int | None = None,
    job_id: int | None = None,
    node_id: str | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> AuditLog:
    """Insert one audit row; flush (not commit) so the row participates in the caller's transaction.

    Call-sites commit at natural transaction boundaries — do not commit here.
    """
    cid = (correlation_id or get_correlation_id() or "").strip() or None
    rid = str(resource_id) if resource_id is not None else None
    row = AuditLog(
        actor_user_id=actor_user_id,
        actor_type=actor_type,
        action=action,
        resource_type=resource_type,
        resource_id=rid,
        workspace_id=workspace_id,
        job_id=job_id,
        node_id=node_id,
        outcome=outcome,
        reason=(reason or "")[:4096] or None,
        metadata_json=dict(metadata or {}),
        correlation_id=cid[:64] if cid else None,
    )
    session.add(row)
    session.flush()
    log_event(
        logger,
        LogEvent.AUDIT_EVENT_RECORDED,
        action=action,
        resource_type=resource_type,
        resource_id=rid,
        outcome=outcome,
        actor_user_id=actor_user_id,
        actor_type=actor_type,
        workspace_id=workspace_id,
        correlation_id=cid,
    )
    return row


def list_audit_logs_for_workspace(
    session: Session,
    *,
    workspace_id: int,
    limit: int = 200,
    offset: int = 0,
) -> list[AuditLog]:
    return list(
        session.exec(
            select(AuditLog)
            .where(AuditLog.workspace_id == workspace_id)
            .order_by(AuditLog.created_at.desc())
            .offset(offset)
            .limit(min(limit, 1000)),
        ).all(),
    )


def list_audit_logs_for_user(
    session: Session,
    *,
    actor_user_id: int,
    limit: int = 200,
    offset: int = 0,
) -> list[AuditLog]:
    return list(
        session.exec(
            select(AuditLog)
            .where(AuditLog.actor_user_id == actor_user_id)
            .order_by(AuditLog.created_at.desc())
            .offset(offset)
            .limit(min(limit, 1000)),
        ).all(),
    )
