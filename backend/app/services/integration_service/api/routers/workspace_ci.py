"""Workspace-scoped CI/CD configuration and trigger routes.

V1 supports GitHub Actions via ``repository_dispatch`` events. A workspace must
first have a CI config (``POST /workspaces/{id}/ci/config``) that names the
GitHub repo to dispatch against.  Triggering (``POST /workspaces/{id}/ci/trigger``)
calls the GitHub API synchronously and records the outcome in ``CITriggerRecord``.

Routes
------
GET    /workspaces/{id}/ci/config   — return the CI config for this workspace
POST   /workspaces/{id}/ci/config   — create or replace the CI config
DELETE /workspaces/{id}/ci/config   — remove CI config
POST   /workspaces/{id}/ci/trigger  — trigger a CI run
GET    /workspaces/{id}/ci/triggers — list recent trigger records
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlmodel import Session, select

from app.libs.db.database import get_db
from app.services.audit_service.enums import AuditAction, AuditActorType, AuditOutcome
from app.services.audit_service.service import record_audit
from app.services.auth_service.api.dependencies import get_current_user
from app.services.auth_service.models import UserAuth
from app.services.integration_service.api.routers.provider_tokens import resolve_provider_token
from app.services.usage_service.enums import UsageEventType
from app.services.usage_service.service import record_usage
from app.services.integration_service.api.schemas import (
    CIConfigRequest,
    CIConfigResponse,
    CITriggerRequest,
    CITriggerResponse,
)
from app.services.integration_service.github_client import GitHubClient, GitHubClientError
from app.services.integration_service.models import CITriggerRecord, WorkspaceCIConfig
from app.services.workspace_service.models import Workspace

_logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workspaces", tags=["workspace-integrations"])


def _get_workspace_owned(session: Session, workspace_id: int, user_id: int) -> Workspace:
    ws = session.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    if ws.owner_user_id != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not your workspace")
    return ws


def _get_ci_config(session: Session, workspace_id: int) -> WorkspaceCIConfig | None:
    return session.exec(
        select(WorkspaceCIConfig).where(WorkspaceCIConfig.workspace_id == workspace_id)
    ).first()


def _ci_config_response(cfg: WorkspaceCIConfig) -> CIConfigResponse:
    return CIConfigResponse(
        ci_config_id=int(cfg.ci_config_id),
        workspace_id=cfg.workspace_id,
        provider=cfg.provider,
        repo_owner=cfg.repo_owner,
        repo_name=cfg.repo_name,
        workflow_file=cfg.workflow_file,
        default_branch=cfg.default_branch,
        is_active=cfg.is_active,
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )


# ── CI config CRUD ────────────────────────────────────────────────────────────

@router.get(
    "/{workspace_id}/ci/config",
    response_model=CIConfigResponse,
    summary="Get CI/CD configuration for this workspace",
)
def get_ci_config(
    workspace_id: int,
    current: UserAuth = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> CIConfigResponse:
    assert current.user_auth_id is not None
    _get_workspace_owned(session, workspace_id, current.user_auth_id)
    cfg = _get_ci_config(session, workspace_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No CI config for this workspace")
    return _ci_config_response(cfg)


@router.post(
    "/{workspace_id}/ci/config",
    response_model=CIConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create or replace CI/CD configuration for this workspace",
)
def upsert_ci_config(
    workspace_id: int,
    body: CIConfigRequest,
    current: UserAuth = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> Response:
    assert current.user_auth_id is not None
    _get_workspace_owned(session, workspace_id, current.user_auth_id)

    now = datetime.now(timezone.utc)
    existing = _get_ci_config(session, workspace_id)
    if existing is not None:
        existing.provider = body.provider
        existing.repo_owner = body.repo_owner
        existing.repo_name = body.repo_name
        existing.workflow_file = body.workflow_file
        existing.default_branch = body.default_branch
        existing.is_active = True
        existing.updated_at = now
        session.add(existing)
        session.commit()
        session.refresh(existing)
        # Return 200 for update (201 is reserved for resource creation).
        from fastapi.encoders import jsonable_encoder  # noqa: PLC0415
        import json  # noqa: PLC0415
        return Response(
            content=json.dumps(jsonable_encoder(_ci_config_response(existing))),
            status_code=status.HTTP_200_OK,
            media_type="application/json",
        )

    cfg = WorkspaceCIConfig(
        workspace_id=workspace_id,
        owner_user_id=current.user_auth_id,
        provider=body.provider,
        repo_owner=body.repo_owner,
        repo_name=body.repo_name,
        workflow_file=body.workflow_file,
        default_branch=body.default_branch,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    return _ci_config_response(cfg)


@router.delete(
    "/{workspace_id}/ci/config",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Remove CI/CD configuration for this workspace",
)
def delete_ci_config(
    workspace_id: int,
    current: UserAuth = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    assert current.user_auth_id is not None
    _get_workspace_owned(session, workspace_id, current.user_auth_id)
    cfg = _get_ci_config(session, workspace_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No CI config for this workspace")
    session.delete(cfg)
    session.commit()


# ── CI trigger ────────────────────────────────────────────────────────────────

@router.post(
    "/{workspace_id}/ci/trigger",
    response_model=CITriggerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Trigger a CI/CD workflow for this workspace",
)
def trigger_ci(
    workspace_id: int,
    body: CITriggerRequest,
    current: UserAuth = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> CITriggerResponse:
    assert current.user_auth_id is not None
    _get_workspace_owned(session, workspace_id, current.user_auth_id)

    cfg = _get_ci_config(session, workspace_id)
    if cfg is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="No CI config for this workspace. Call POST /workspaces/{id}/ci/config first.",
        )
    if not cfg.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="CI config is inactive")

    # Resolve provider token.
    provider_key = body.use_provider or "github"
    provider_token = resolve_provider_token(session, current.user_auth_id, provider_key)
    if not provider_token:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"No {provider_key} provider token found. "
                "Connect your GitHub account at POST /auth/provider-tokens/github/connect first."
            ),
        )

    ref = body.ref or cfg.default_branch
    # Keep trusted fields in top-level payload; user inputs are nested under "inputs"
    # to prevent callers from overwriting workspace_id or ref.
    client_payload: dict = {
        "workspace_id": workspace_id,
        "ref": ref,
        "inputs": body.inputs or {},
    }

    now = datetime.now(timezone.utc)
    record = CITriggerRecord(
        workspace_id=workspace_id,
        owner_user_id=current.user_auth_id,
        provider=cfg.provider,
        event_type=body.event_type,
        ref=ref,
        inputs_json=body.inputs,
        triggered_at=now,
        status="triggered",
    )

    try:
        client = GitHubClient(provider_token)
        client.trigger_repository_dispatch(
            cfg.repo_owner,
            cfg.repo_name,
            event_type=body.event_type,
            client_payload=client_payload,
        )
        _logger.info(
            "ci_trigger_dispatched",
            extra={
                "workspace_id": workspace_id,
                "repo": f"{cfg.repo_owner}/{cfg.repo_name}",
                "event_type": body.event_type,
            },
        )
    except GitHubClientError as exc:
        record.status = "failed"
        record.error_msg = str(exc)[:512]
        session.add(record)
        record_audit(
            session,
            action=AuditAction.INTEGRATION_CI_TRIGGER_FAILED.value,
            resource_type="workspace",
            resource_id=workspace_id,
            actor_user_id=current.user_auth_id,
            actor_type=AuditActorType.USER.value,
            outcome=AuditOutcome.FAILURE.value,
            workspace_id=workspace_id,
            reason=str(exc)[:256],
        )
        session.commit()
        session.refresh(record)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"GitHub dispatch failed: {exc}",
        ) from exc

    record.status = "succeeded"
    session.add(record)
    record_audit(
        session,
        action=AuditAction.INTEGRATION_CI_TRIGGERED.value,
        resource_type="workspace",
        resource_id=workspace_id,
        actor_user_id=current.user_auth_id,
        actor_type=AuditActorType.USER.value,
        outcome=AuditOutcome.SUCCESS.value,
        workspace_id=workspace_id,
        metadata={"event_type": body.event_type, "ref": ref, "repo": f"{cfg.repo_owner}/{cfg.repo_name}"},
    )
    record_usage(
        session,
        event_type=UsageEventType.CI_TRIGGERED.value,
        workspace_id=workspace_id,
        owner_user_id=current.user_auth_id,
    )
    session.commit()
    session.refresh(record)

    return CITriggerResponse(
        trigger_id=int(record.trigger_id),
        workspace_id=workspace_id,
        status=record.status,
        event_type=record.event_type,
        ref=record.ref,
        triggered_at=record.triggered_at,
        error_msg=record.error_msg,
    )


@router.get(
    "/{workspace_id}/ci/triggers",
    response_model=list[CITriggerResponse],
    summary="List recent CI/CD trigger records for this workspace",
)
def list_ci_triggers(
    workspace_id: int,
    limit: int = Query(20, ge=1, le=100),
    current: UserAuth = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[CITriggerResponse]:
    assert current.user_auth_id is not None
    _get_workspace_owned(session, workspace_id, current.user_auth_id)
    rows = session.exec(
        select(CITriggerRecord)
        .where(CITriggerRecord.workspace_id == workspace_id)
        .order_by(CITriggerRecord.triggered_at.desc())
        .limit(limit)
    ).all()
    return [
        CITriggerResponse(
            trigger_id=int(r.trigger_id),
            workspace_id=workspace_id,
            status=r.status,
            event_type=r.event_type,
            ref=r.ref,
            triggered_at=r.triggered_at,
            error_msg=r.error_msg,
        )
        for r in rows
    ]
