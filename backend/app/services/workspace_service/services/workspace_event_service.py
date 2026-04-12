"""Persist and stream workspace control-plane events (V1: DB append + polling SSE)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select

from app.services.workspace_service.errors import WorkspaceNotFoundError
from app.services.workspace_service.models import Workspace, WorkspaceEvent


class WorkspaceStreamEventType:
    """Normalized SSE / persistence event type strings (not a DB enum)."""

    INTENT_QUEUED = "controlplane.intent_queued"
    JOB_RUNNING = "controlplane.job_running"
    JOB_SUCCEEDED = "controlplane.job_succeeded"
    JOB_FAILED = "controlplane.job_failed"
    JOB_RETRY_SCHEDULED = "controlplane.job_retry_scheduled"
    JOB_RETRY_EXHAUSTED = "controlplane.job_retry_exhausted"
    RECONCILE_RETRY_SCHEDULED = "controlplane.reconcile_retry_scheduled"
    RECONCILE_FAILED_TERMINAL = "controlplane.reconcile_failed_terminal"
    RECONCILE_STARTED = "controlplane.reconcile_started"
    RECONCILE_FIXED_ROUTE = "controlplane.reconcile_fixed_route"
    RECONCILE_FIXED_RUNTIME = "controlplane.reconcile_fixed_runtime"
    RECONCILE_CLEANED_ORPHAN = "controlplane.reconcile_cleaned_orphan"
    RECONCILE_NOOP = "controlplane.reconcile_noop"
    RECONCILE_FAILED = "controlplane.reconcile_failed"

    SNAPSHOT_CREATED = "workspace.snapshot.created"
    SNAPSHOT_FAILED = "workspace.snapshot.failed"
    SNAPSHOT_RESTORED = "workspace.snapshot.restored"
    SNAPSHOT_DELETED = "workspace.snapshot.deleted"


SSE_POLL_INTERVAL_SEC = 1.0
# Max events per SSE poll; large backlogs are drained across multiple polls via ``after_id``.
EVENT_PAGE_LIMIT = 250


def record_workspace_event(
    session: Session,
    *,
    workspace_id: int,
    event_type: str,
    status: str | None = None,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    row = WorkspaceEvent(
        workspace_id=workspace_id,
        event_type=event_type,
        status=status,
        message=message,
        payload_json=dict(payload or {}),
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    assert row.workspace_event_id is not None
    return row.workspace_event_id


def list_workspace_events(
    session: Session,
    *,
    workspace_id: int,
    owner_user_id: int,
    after_id: int = 0,
    limit: int = 500,
) -> list[WorkspaceEvent]:
    ws = session.get(Workspace, workspace_id)
    if ws is None or ws.owner_user_id != owner_user_id:
        raise WorkspaceNotFoundError("Workspace not found")
    lim = min(max(1, limit), 1000)
    stmt = (
        select(WorkspaceEvent)
        .where(
            WorkspaceEvent.workspace_id == workspace_id,
            WorkspaceEvent.workspace_event_id > after_id,
        )
        .order_by(WorkspaceEvent.workspace_event_id.asc())
        .limit(lim)
    )
    return list(session.exec(stmt).all())


def assert_workspace_owner(session: Session, workspace_id: int, owner_user_id: int) -> None:
    ws = session.get(Workspace, workspace_id)
    if ws is None or ws.owner_user_id != owner_user_id:
        raise WorkspaceNotFoundError("Workspace not found")


def event_to_sse_dict(event: WorkspaceEvent) -> dict[str, Any]:
    return {
        "id": event.workspace_event_id,
        "workspace_id": event.workspace_id,
        "event_type": event.event_type,
        "status": event.status,
        "message": event.message,
        "payload": event.payload_json or {},
        "created_at": event.created_at.isoformat(),
    }


def format_sse_data_line(event: WorkspaceEvent) -> str:
    return f"data: {json.dumps(event_to_sse_dict(event), default=str)}\n\n"
