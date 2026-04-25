"""Workspace control-plane routes (V1: create, list, get, lifecycle intents)."""

import asyncio
import json
from collections.abc import AsyncIterator

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlmodel import Session

from app.libs.common.config import get_settings

from app.libs.db.database import get_db, get_engine
from app.libs.security.rate_limit import sse_rate_limit
from app.services.auth_service.api.dependencies import get_current_user
from app.services.auth_service.models import UserAuth
from app.services.workspace_service.api.schemas import (
    CreateWorkspaceAcceptedResponse,
    CreateWorkspaceRequest,
    PatchWorkspaceUpdateRequest,
    WorkspaceAISecretInput,
    WorkspaceAccessResponse,
    WorkspaceAttachRequest,
    WorkspaceAttachResponse,
    WorkspaceDetailResponse,
    WorkspaceIntentAcceptedResponse,
    WorkspaceListResponse,
    WorkspaceSecretMutationResponse,
)
from app.services.workspace_service.errors import (
    WorkspaceAccessDeniedError,
    WorkspaceBusyError,
    WorkspaceGatewayUnavailableError,
    WorkspaceInvalidStateError,
    WorkspaceNotFoundError,
    WorkspaceServiceError,
)
from app.services.workspace_service.services import workspace_intent_service
from app.services.workspace_service.services.workspace_secret_service import (
    delete_workspace_ai_secret,
    upsert_workspace_ai_secret,
)
from app.services.workspace_service.services.workspace_event_service import (
    EVENT_PAGE_LIMIT,
    SSE_POLL_INTERVAL_SEC,
    assert_workspace_owner,
    format_sse_data_line,
    list_workspace_events,
)
from app.services.workspace_service.services.workspace_session_service import (
    WORKSPACE_SESSION_COOKIE_NAME,
    WORKSPACE_SESSION_HTTP_HEADER,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _correlation_id_from_request(request: Request) -> str | None:
    return getattr(request.state, "correlation_id", None)


def _raise_workspace_http(exc: WorkspaceServiceError) -> None:
    if isinstance(exc, WorkspaceNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found") from exc
    if isinstance(exc, WorkspaceAccessDeniedError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if isinstance(exc, WorkspaceGatewayUnavailableError):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
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
        workspace_session_id=out.workspace_session_id,
        session_token=out.session_token,
        session_expires_at=out.session_expires_at,
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


@router.put(
    "/{workspace_id}/secrets/ai",
    response_model=WorkspaceSecretMutationResponse,
    status_code=status.HTTP_200_OK,
    summary="Upsert encrypted AI secret for a workspace",
)
def put_workspace_ai_secret(
    workspace_id: int,
    body: WorkspaceAISecretInput,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> WorkspaceSecretMutationResponse:
    assert current.user_auth_id is not None
    try:
        upsert_workspace_ai_secret(
            session,
            workspace_id=workspace_id,
            owner_user_id=current.user_auth_id,
            provider=body.provider,
            api_key=body.api_key,
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        detail = str(exc)
        if detail == "workspace_not_found_or_not_owned":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found") from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc
    return WorkspaceSecretMutationResponse(
        workspace_id=workspace_id,
        message="Workspace AI secret saved.",
    )


@router.delete(
    "/{workspace_id}/secrets/ai/{provider}",
    response_model=WorkspaceSecretMutationResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete encrypted AI secret for a workspace",
)
def delete_workspace_ai_secret_route(
    workspace_id: int,
    provider: str,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
) -> WorkspaceSecretMutationResponse:
    assert current.user_auth_id is not None
    try:
        delete_workspace_ai_secret(
            session,
            workspace_id=workspace_id,
            owner_user_id=current.user_auth_id,
            provider=provider,
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        detail = str(exc)
        if detail == "workspace_not_found_or_not_owned":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found") from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc
    return WorkspaceSecretMutationResponse(
        workspace_id=workspace_id,
        message="Workspace AI secret deleted.",
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
    summary="Attach to workspace (session when RUNNING)",
    description=(
        "Creates a workspace session when the workspace is RUNNING and runtime placement exists. "
        "Does not start the workspace — use POST /workspaces/start/{id} first. "
        "Returns a one-time session_token; send it as header X-DevNest-Workspace-Session on GET /workspaces/{id}/access. "
        "When ``DEVNEST_GATEWAY_ENABLED`` and ``DEVNEST_GATEWAY_AUTH_ENABLED`` are true, also sets HttpOnly cookie "
        f"``{WORKSPACE_SESSION_COOKIE_NAME}`` (Domain derived from ``DEVNEST_BASE_DOMAIN``) so browser navigations "
        "to the workspace Traefik host include a session for ForwardAuth."
    ),
)
def post_workspace_attach(
    request: Request,
    workspace_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
    body: Annotated[WorkspaceAttachRequest | None, Body()] = None,
) -> JSONResponse:
    assert current.user_auth_id is not None
    uid = current.user_auth_id
    meta = body.client_metadata if body else {}
    try:
        out = workspace_intent_service.request_attach_workspace(
            session,
            workspace_id=workspace_id,
            owner_user_id=uid,
            requested_by_user_id=uid,
            client_metadata=meta,
            correlation_id=_correlation_id_from_request(request),
        )
    except WorkspaceServiceError as exc:
        _raise_workspace_http(exc)
    payload = _attach_response(out)
    resp = JSONResponse(status_code=status.HTTP_200_OK, content=payload.model_dump(mode="json"))
    settings = get_settings()
    if (
        settings.devnest_gateway_enabled
        and settings.devnest_gateway_auth_enabled
        and out.accepted
        and (out.session_token or "").strip()
    ):
        req_https = request.url.scheme == "https"
        max_age = max(60, int(settings.workspace_session_ttl_seconds))
        tok = out.session_token.strip()
        dom = (settings.devnest_base_domain or "").strip().strip(".")
        if dom and "." in dom:
            resp.set_cookie(
                WORKSPACE_SESSION_COOKIE_NAME,
                tok,
                max_age=max_age,
                httponly=True,
                secure=req_https,
                samesite="none" if req_https else "lax",
                domain=f".{dom}",
            )
        else:
            resp.set_cookie(
                WORKSPACE_SESSION_COOKIE_NAME,
                tok,
                max_age=max_age,
                httponly=True,
                secure=req_https,
                samesite="none" if req_https else "lax",
            )
    return resp


@router.get(
    "/{workspace_id}/events",
    summary="Stream workspace control-plane events (SSE)",
    description=(
        "Server-Sent Events stream of persisted control-plane events for this workspace. "
        "Supply ``last_event_id`` to resume from a known cursor and avoid replaying history. "
        "The stream uses an in-process push-notification bus when available, falling back to "
        "periodic DB polling (``EVENT_BUS_WAIT_TIMEOUT_SEC`` cadence) for multi-process deployments."
    ),
    dependencies=[Depends(sse_rate_limit)],
)
async def stream_workspace_events(
    workspace_id: int,
    request: Request,
    current: UserAuth = Depends(get_current_user),
    last_event_id: int = Query(0, ge=0, description="Resume cursor: only return events with id > this value"),
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

    # Register with the in-process event bus to receive push notifications.
    # Multi-worker safety: the in-process bus only notifies listeners in the same OS process.
    # Cross-worker events (produced by a different gunicorn worker) are delivered via the DB
    # polling fallback below, bounded by devnest_sse_poll_interval_seconds (default 2s).
    from app.libs.events.workspace_event_bus import get_event_bus  # noqa: PLC0415
    bus = get_event_bus()
    notification_event = bus.subscribe(workspace_id)
    _poll_interval = get_settings().devnest_sse_poll_interval_seconds

    async def event_stream() -> AsyncIterator[str]:
        nonlocal last_event_id
        last_id = last_event_id
        try:
            while True:
                if await request.is_disconnected():
                    break

                # Wait for an in-process push notification OR the configurable poll timeout.
                # Same-process events wake the loop immediately (near-zero latency).
                # Cross-worker events are delivered within _poll_interval seconds via DB poll.
                try:
                    await asyncio.wait_for(notification_event.wait(), timeout=_poll_interval)
                except asyncio.TimeoutError:
                    pass  # Fallback DB poll — no same-process notification received.

                # Reset so next iteration blocks again.
                notification_event.clear()

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
        finally:
            bus.unsubscribe(workspace_id, notification_event)

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
        "Returns gateway/runtime metadata when RUNNING, runtime is ready, and X-DevNest-Workspace-Session "
        "matches an active session from POST /workspaces/attach/{id}. "
        "Refreshes session last_seen_at. Does not enqueue lifecycle jobs."
    ),
)
def get_workspace_access_route(
    request: Request,
    workspace_id: int,
    session: Session = Depends(get_db),
    current: UserAuth = Depends(get_current_user),
    x_devnest_workspace_session: Annotated[
        str | None,
        Header(alias=WORKSPACE_SESSION_HTTP_HEADER, description="Opaque token from POST /workspaces/attach/{id}"),
    ] = None,
) -> WorkspaceAccessResponse:
    assert current.user_auth_id is not None
    try:
        out = workspace_intent_service.get_workspace_access(
            session,
            workspace_id=workspace_id,
            owner_user_id=current.user_auth_id,
            workspace_session_token=x_devnest_workspace_session,
            correlation_id=_correlation_id_from_request(request),
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
