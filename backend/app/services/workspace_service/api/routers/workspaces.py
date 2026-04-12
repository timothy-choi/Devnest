"""Workspace control-plane routes (V1: create, list, get, lifecycle intents)."""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from app.libs.db.database import get_db, get_engine
from app.services.auth_service.api.dependencies import get_current_user
from app.services.auth_service.models import UserAuth
from app.services.workspace_service.api.schemas import (
    CreateWorkspaceAcceptedResponse,
    CreateWorkspaceRequest,
    PatchWorkspaceUpdateRequest,
    WorkspaceAccessResponse,
    WorkspaceAttachResponse,
    WorkspaceDetailResponse,
    WorkspaceIntentAcceptedResponse,
    WorkspaceListResponse,
)
from app.services.workspace_service.errors import (
    WorkspaceBusyError,
    WorkspaceInvalidStateError,
    WorkspaceNotFoundError,
    WorkspaceServiceError,
)
from app.services.workspace_service.services import workspace_intent_service
from app.services.workspace_service.services.workspace_event_service import (
    EVENT_PAGE_LIMIT,
    SSE_POLL_INTERVAL_SEC,
    assert_workspace_owner,
    format_sse_data_line,
    list_workspace_events,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _correlation_id_from_request(request: Request) -> str | None:
    return getattr(request.state, "correlation_id", None)


def _raise_workspace_http(exc: WorkspaceServiceError) -> None:
    if isinstance(exc, WorkspaceNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found") from exc
    if isinstance(exc, WorkspaceBusyError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, WorkspaceInvalidStateError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _intent_response(
    out: workspace_intent_service.WorkspaceIntentResult,
) -> WorkspaceIntentAcceptedResponse:
    return WorkspaceIntentAcceptedResponse(
        workspace_id=out.workspace_id,
        status=out.status,
        job_id=out.job_id,
        job_type=out.job_type,
        requested_config_version=out.requested_config_version,
        issues=list(out.issues),
    )


def _access_response(out: workspace_intent_service.WorkspaceAccessResult) -> WorkspaceAccessResponse:
    return WorkspaceAccessResponse(
        workspace_id=out.workspace_id,
        success=out.success,
        status=out.status,
        runtime_ready=out.runtime_ready,
        endpoint_ref=out.endpoint_ref,
        public_host=out.public_host,
        internal_endpoint=out.internal_endpoint,
        gateway_url=out.gateway_url,
        issues=list(out.issues),
    )


def _attach_response(out: workspace_intent_service.WorkspaceAttachResult) -> WorkspaceAttachResponse:
    return WorkspaceAttachResponse(
        workspace_id=out.workspace_id,
        accepted=out.accepted,
        status=out.status,
        runtime_ready=out.runtime_ready,
        active_sessions_count=out.active_sessions_count,
        endpoint_ref=out.endpoint_ref,
        public_host=out.public_host,
        internal_endpoint=out.internal_endpoint,
        gateway_url=out.gateway_url,
        issues=list(out.issues),
    )


@router.post(
    "",
    response_model=CreateWorkspaceAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create workspace (accepted)",
)
def post_workspace(
    request: Request,
    body: CreateWorkspaceRequest,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> CreateWorkspaceAcceptedResponse:
    assert current.user_auth_id is not None
    out = workspace_intent_service.create_workspace(
        session,
        owner_user_id=current.user_auth_id,
        body=body,
        correlation_id=_correlation_id_from_request(request),
    )
    return CreateWorkspaceAcceptedResponse(
        workspace_id=out.workspace_id,
        status=out.status,
        config_version=out.config_version,
        job_id=out.job_id,
    )


@router.get(
    "",
    response_model=WorkspaceListResponse,
    status_code=status.HTTP_200_OK,
    summary="List workspaces for the current user",
)
def get_workspaces(
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> WorkspaceListResponse:
    assert current.user_auth_id is not None
    items, total = workspace_intent_service.list_workspaces(
        session,
        owner_user_id=current.user_auth_id,
        skip=skip,
        limit=limit,
    )
    return WorkspaceListResponse(items=items, total=total)


@router.post(
    "/start/{workspace_id}",
    response_model=WorkspaceIntentAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request workspace start (accepted)",
)
def post_workspace_start(
    request: Request,
    workspace_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> WorkspaceIntentAcceptedResponse:
    assert current.user_auth_id is not None
    uid = current.user_auth_id
    try:
        out = workspace_intent_service.request_start_workspace(
            session,
            workspace_id=workspace_id,
            owner_user_id=uid,
            requested_by_user_id=uid,
            correlation_id=_correlation_id_from_request(request),
        )
    except WorkspaceServiceError as exc:
        _raise_workspace_http(exc)
    return _intent_response(out)


@router.post(
    "/stop/{workspace_id}",
    response_model=WorkspaceIntentAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request workspace stop (accepted)",
)
def post_workspace_stop(
    request: Request,
    workspace_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> WorkspaceIntentAcceptedResponse:
    assert current.user_auth_id is not None
    uid = current.user_auth_id
    try:
        out = workspace_intent_service.request_stop_workspace(
            session,
            workspace_id=workspace_id,
            owner_user_id=uid,
            requested_by_user_id=uid,
            correlation_id=_correlation_id_from_request(request),
        )
    except WorkspaceServiceError as exc:
        _raise_workspace_http(exc)
    return _intent_response(out)


@router.post(
    "/restart/{workspace_id}",
    response_model=WorkspaceIntentAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request workspace restart (accepted)",
)
def post_workspace_restart(
    request: Request,
    workspace_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> WorkspaceIntentAcceptedResponse:
    assert current.user_auth_id is not None
    uid = current.user_auth_id
    try:
        out = workspace_intent_service.request_restart_workspace(
            session,
            workspace_id=workspace_id,
            owner_user_id=uid,
            requested_by_user_id=uid,
            correlation_id=_correlation_id_from_request(request),
        )
    except WorkspaceServiceError as exc:
        _raise_workspace_http(exc)
    return _intent_response(out)


@router.delete(
    "/{workspace_id}",
    response_model=WorkspaceIntentAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request workspace delete (accepted)",
)
def delete_workspace(
    request: Request,
    workspace_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> WorkspaceIntentAcceptedResponse:
    assert current.user_auth_id is not None
    uid = current.user_auth_id
    try:
        out = workspace_intent_service.request_delete_workspace(
            session,
            workspace_id=workspace_id,
            owner_user_id=uid,
            requested_by_user_id=uid,
            correlation_id=_correlation_id_from_request(request),
        )
    except WorkspaceServiceError as exc:
        _raise_workspace_http(exc)
    return _intent_response(out)


@router.patch(
    "/{workspace_id}",
    response_model=WorkspaceIntentAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request workspace config update (accepted)",
)
def patch_workspace_update(
    request: Request,
    workspace_id: int,
    body: PatchWorkspaceUpdateRequest,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> WorkspaceIntentAcceptedResponse:
    assert current.user_auth_id is not None
    uid = current.user_auth_id
    try:
        out = workspace_intent_service.request_update_workspace(
            session,
            workspace_id=workspace_id,
            owner_user_id=uid,
            requested_by_user_id=uid,
            runtime=body.runtime,
            correlation_id=_correlation_id_from_request(request),
        )
    except WorkspaceServiceError as exc:
        _raise_workspace_http(exc)
    return _intent_response(out)


@router.post(
    "/attach/{workspace_id}",
    response_model=WorkspaceAttachResponse,
    status_code=status.HTTP_200_OK,
    summary="Attach to workspace (access when RUNNING)",
    description=(
        "Grants access when the workspace is RUNNING and runtime placement exists. "
        "Does not start the workspace — use POST /workspaces/start/{id} first. "
        "V1 bumps active_sessions_count as a lightweight session surrogate."
    ),
)
def post_workspace_attach(
    workspace_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> WorkspaceAttachResponse:
    assert current.user_auth_id is not None
    uid = current.user_auth_id
    try:
        out = workspace_intent_service.request_attach_workspace(
            session,
            workspace_id=workspace_id,
            owner_user_id=uid,
            requested_by_user_id=uid,
        )
    except WorkspaceServiceError as exc:
        _raise_workspace_http(exc)
    return _attach_response(out)


@router.get(
    "/{workspace_id}/events",
    summary="Stream workspace control-plane events (SSE)",
    description=(
        "Server-Sent Events stream of persisted control-plane events for this workspace. "
        "Replays stored events (``after_id`` cursor) then polls the database on an interval (V1; no broker). "
        "Use this to observe transactional status and asynchronous job outcomes alongside "
        "POST /workspaces/start/{id} and related intents."
    ),
)
async def stream_workspace_events(
    workspace_id: int,
    request: Request,
    current: UserAuth = Depends(get_current_user),
) -> StreamingResponse:
    assert current.user_auth_id is not None
    uid = current.user_auth_id
    engine = get_engine()

    def _verify_owner() -> None:
        with Session(engine) as session:
            assert_workspace_owner(session, workspace_id, uid)

    try:
        await asyncio.to_thread(_verify_owner)
    except WorkspaceNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found") from None

    async def event_stream() -> AsyncIterator[str]:
        last_id = 0
        while True:
            if await request.is_disconnected():
                break

            def _fetch_page() -> list:
                with Session(engine) as session:
                    return list_workspace_events(
                        session,
                        workspace_id=workspace_id,
                        owner_user_id=uid,
                        after_id=last_id,
                        limit=EVENT_PAGE_LIMIT,
                    )

            try:
                events = await asyncio.to_thread(_fetch_page)
            except WorkspaceNotFoundError:
                yield f"data: {json.dumps({'error': 'workspace_not_found'})}\n\n"
                break

            for ev in events:
                eid = ev.workspace_event_id or 0
                last_id = max(last_id, eid)
                yield format_sse_data_line(ev)

            await asyncio.sleep(SSE_POLL_INTERVAL_SEC)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/{workspace_id}/access",
    response_model=WorkspaceAccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Get workspace access coordinates",
    description=(
        "Returns endpoint metadata when RUNNING and runtime is placed. "
        "Read-only; does not enqueue jobs or increment sessions. "
        "Use POST /workspaces/attach/{id} to record an attach / bump session count."
    ),
)
def get_workspace_access_route(
    workspace_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> WorkspaceAccessResponse:
    assert current.user_auth_id is not None
    try:
        out = workspace_intent_service.get_workspace_access(
            session,
            workspace_id=workspace_id,
            owner_user_id=current.user_auth_id,
        )
    except WorkspaceServiceError as exc:
        _raise_workspace_http(exc)
    return _access_response(out)


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get workspace by id",
)
def get_workspace(
    workspace_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> WorkspaceDetailResponse:
    assert current.user_auth_id is not None
    detail = workspace_intent_service.get_workspace(
        session,
        workspace_id=workspace_id,
        owner_user_id=current.user_auth_id,
    )
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return detail
