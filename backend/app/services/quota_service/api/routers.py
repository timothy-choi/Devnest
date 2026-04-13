"""Internal admin routes for quota management."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlmodel import Session, select

from app.libs.db.database import get_db
from app.libs.security.dependencies import require_internal_api_key
from app.libs.security.internal_auth import InternalApiScope

from ..enums import ScopeType
from ..models import Quota
from .schemas import (
    CreateQuotaRequest,
    PatchQuotaRequest,
    QuotaListResponse,
    QuotaResponse,
)

router = APIRouter(
    prefix="/internal/quotas",
    tags=["internal-quotas"],
    dependencies=[Depends(require_internal_api_key(InternalApiScope.INFRASTRUCTURE))],
)


def _to_response(q: Quota) -> QuotaResponse:
    return QuotaResponse(
        quota_id=q.quota_id,  # type: ignore[arg-type]
        scope_type=q.scope_type,
        scope_id=q.scope_id,
        max_workspaces=q.max_workspaces,
        max_running_workspaces=q.max_running_workspaces,
        max_cpu=q.max_cpu,
        max_memory_mb=q.max_memory_mb,
        max_storage_mb=q.max_storage_mb,
        max_sessions=q.max_sessions,
        max_snapshots=q.max_snapshots,
        max_runtime_hours=q.max_runtime_hours,
        created_at=q.created_at,
        updated_at=q.updated_at,
    )


@router.get("", response_model=QuotaListResponse)
def list_quotas(
    scope_type: str | None = Query(default=None),
    scope_id: int | None = Query(default=None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> QuotaListResponse:
    stmt = select(Quota)
    if scope_type is not None:
        stmt = stmt.where(Quota.scope_type == scope_type)
    if scope_id is not None:
        stmt = stmt.where(Quota.scope_id == scope_id)

    total = int(
        session.exec(select(func.count()).select_from(stmt.subquery())).one()
    )
    rows = list(
        session.exec(stmt.order_by(Quota.created_at).offset(offset).limit(limit)).all()
    )
    return QuotaListResponse(items=[_to_response(q) for q in rows], total=total)


@router.post("", response_model=QuotaResponse, status_code=status.HTTP_201_CREATED)
def create_quota(
    body: CreateQuotaRequest,
    session: Session = Depends(get_db),
) -> QuotaResponse:
    try:
        scope = ScopeType(body.scope_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if scope == ScopeType.GLOBAL and body.scope_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Global quotas must have scope_id=null",
        )
    if scope in (ScopeType.USER, ScopeType.WORKSPACE) and body.scope_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{scope.value.capitalize()} quotas require a non-null scope_id",
        )

    now = datetime.now(timezone.utc)
    q = Quota(
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        max_workspaces=body.max_workspaces,
        max_running_workspaces=body.max_running_workspaces,
        max_cpu=body.max_cpu,
        max_memory_mb=body.max_memory_mb,
        max_storage_mb=body.max_storage_mb,
        max_sessions=body.max_sessions,
        max_snapshots=body.max_snapshots,
        max_runtime_hours=body.max_runtime_hours,
        created_at=now,
        updated_at=now,
    )
    session.add(q)
    session.commit()
    session.refresh(q)
    return _to_response(q)


@router.patch("/{quota_id}", response_model=QuotaResponse)
def patch_quota(
    quota_id: int,
    body: PatchQuotaRequest,
    session: Session = Depends(get_db),
) -> QuotaResponse:
    q = session.get(Quota, quota_id)
    if q is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quota not found")

    # Only update fields that were explicitly set in the request body
    update = body.model_dump(exclude_unset=True)
    for field, value in update.items():
        setattr(q, field, value)
    q.updated_at = datetime.now(timezone.utc)
    session.add(q)
    session.commit()
    session.refresh(q)
    return _to_response(q)


@router.delete("/{quota_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_quota(
    quota_id: int,
    session: Session = Depends(get_db),
) -> None:
    q = session.get(Quota, quota_id)
    if q is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quota not found")
    session.delete(q)
    session.commit()
