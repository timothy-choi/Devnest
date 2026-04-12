"""Internal admin audit log read endpoints.

Access requires ``X-Internal-API-Key`` with the ``INFRASTRUCTURE`` scope (re-uses infra scope
so there is no new credential surface to rotate separately; TODO: add an ``AUDIT`` scope if
needed for granular separation).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.libs.db.database import get_db
from app.libs.security.dependencies import require_internal_api_key
from app.libs.security.internal_auth import InternalApiScope

from ..service import list_audit_logs_for_user, list_audit_logs_for_workspace
from .schemas import AuditLogListResponse, AuditLogResponse

router = APIRouter(
    prefix="/internal/audit-logs",
    tags=["audit"],
    dependencies=[Depends(require_internal_api_key(InternalApiScope.INFRASTRUCTURE))],
)


def _to_response(row) -> AuditLogResponse:
    return AuditLogResponse(
        audit_log_id=row.audit_log_id,
        actor_user_id=row.actor_user_id,
        actor_type=row.actor_type,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        workspace_id=row.workspace_id,
        job_id=row.job_id,
        node_id=row.node_id,
        outcome=row.outcome,
        reason=row.reason,
        metadata=row.metadata_json,
        correlation_id=row.correlation_id,
        created_at=row.created_at,
    )


@router.get("/workspaces/{workspace_id}", response_model=AuditLogListResponse)
def get_workspace_audit_logs(
    workspace_id: int,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> AuditLogListResponse:
    rows = list_audit_logs_for_workspace(
        session,
        workspace_id=workspace_id,
        limit=limit,
        offset=offset,
    )
    return AuditLogListResponse(items=[_to_response(r) for r in rows], total=len(rows))


@router.get("/users/{user_id}", response_model=AuditLogListResponse)
def get_user_audit_logs(
    user_id: int,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> AuditLogListResponse:
    rows = list_audit_logs_for_user(
        session,
        actor_user_id=user_id,
        limit=limit,
        offset=offset,
    )
    return AuditLogListResponse(items=[_to_response(r) for r in rows], total=len(rows))
