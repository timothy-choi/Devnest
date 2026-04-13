"""Internal admin routes for policy management.

Access requires ``X-Internal-API-Key`` with ``INFRASTRUCTURE`` scope.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlmodel import Session, select

from app.libs.db.database import get_db
from app.libs.security.dependencies import require_internal_api_key
from app.libs.security.internal_auth import InternalApiScope

from ..enums import PolicyType, ScopeType
from ..models import Policy
from .schemas import (
    CreatePolicyRequest,
    PatchPolicyRequest,
    PolicyListResponse,
    PolicyResponse,
)

router = APIRouter(
    prefix="/internal/policies",
    tags=["internal-policies"],
    dependencies=[Depends(require_internal_api_key(InternalApiScope.INFRASTRUCTURE))],
)


def _to_response(p: Policy) -> PolicyResponse:
    return PolicyResponse(
        policy_id=p.policy_id,  # type: ignore[arg-type]
        name=p.name,
        description=p.description,
        policy_type=p.policy_type,
        scope_type=p.scope_type,
        scope_id=p.scope_id,
        rules_json=p.rules_json or {},
        is_active=p.is_active,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.get("", response_model=PolicyListResponse)
def list_policies(
    scope_type: str | None = Query(default=None, description="Filter by scope_type"),
    is_active: bool | None = Query(default=None, description="Filter by is_active"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> PolicyListResponse:
    stmt = select(Policy)
    if scope_type is not None:
        stmt = stmt.where(Policy.scope_type == scope_type)
    if is_active is not None:
        stmt = stmt.where(Policy.is_active == is_active)

    total = int(
        session.exec(
            select(func.count()).select_from(stmt.subquery())
        ).one()
    )
    rows = list(
        session.exec(stmt.order_by(Policy.created_at).offset(offset).limit(limit)).all()
    )
    return PolicyListResponse(items=[_to_response(p) for p in rows], total=total)


@router.post("", response_model=PolicyResponse, status_code=status.HTTP_201_CREATED)
def create_policy(
    body: CreatePolicyRequest,
    session: Session = Depends(get_db),
) -> PolicyResponse:
    try:
        _ = PolicyType(body.policy_type)
        scope = ScopeType(body.scope_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if scope == ScopeType.GLOBAL and body.scope_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Global policies must have scope_id=null",
        )
    if scope in (ScopeType.USER, ScopeType.WORKSPACE) and body.scope_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{scope.value.capitalize()} policies require a non-null scope_id",
        )

    now = datetime.now(timezone.utc)
    p = Policy(
        name=body.name,
        description=body.description,
        policy_type=body.policy_type,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        rules_json=body.rules.model_dump(),
        is_active=body.is_active,
        created_at=now,
        updated_at=now,
    )
    session.add(p)
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Policy with name '{body.name}' already exists",
            ) from exc
        raise
    session.refresh(p)
    return _to_response(p)


@router.patch("/{policy_id}", response_model=PolicyResponse)
def patch_policy(
    policy_id: int,
    body: PatchPolicyRequest,
    session: Session = Depends(get_db),
) -> PolicyResponse:
    p = session.get(Policy, policy_id)
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")
    if body.description is not None:
        p.description = body.description
    if body.rules is not None:
        p.rules_json = body.rules.model_dump()
    if body.is_active is not None:
        p.is_active = body.is_active
    p.updated_at = datetime.now(timezone.utc)
    session.add(p)
    session.commit()
    session.refresh(p)
    return _to_response(p)


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_policy(
    policy_id: int,
    session: Session = Depends(get_db),
) -> None:
    p = session.get(Policy, policy_id)
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")
    session.delete(p)
    session.commit()
