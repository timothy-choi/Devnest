"""WebSocket interactive terminal endpoint for workspace containers.

Authentication
--------------
WebSocket connections cannot carry HTTP ``Authorization: Bearer`` headers in
browser environments. Instead, the workspace session token is passed as a
URL query parameter ``?token=<plain_token>``. The token is validated server-side
before the WebSocket upgrade is accepted; on failure the connection is closed with
code 4001 (policy violation).

The terminal attaches to the running workspace container using the
``NodeExecutionBundle`` for that workspace (see
:mod:`app.services.integration_service.terminal_service`).

Endpoint
--------
WS /workspaces/{workspace_id}/terminal?token=<session_token>
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.libs.db.database import get_db
from app.services.audit_service.enums import AuditAction, AuditActorType, AuditOutcome
from app.services.audit_service.service import record_audit
from app.services.auth_service.services.auth_token import decode_access_user_id
from app.services.auth_service.services.auth_profile_service import get_user_auth_entry
from app.services.integration_service.terminal_service import TerminalError, relay_terminal
from app.services.node_execution_service.factory import resolve_node_execution_bundle
from app.services.usage_service.enums import UsageEventType
from app.services.usage_service.service import record_usage
from app.services.workspace_service.api.schemas.workspace_schemas import get_workspace_features
from app.services.workspace_service.errors import WorkspaceAccessDeniedError
from app.services.workspace_service.models import Workspace, WorkspaceConfig, WorkspaceRuntime
from app.services.workspace_service.models.enums import WorkspaceStatus
from app.services.workspace_service.services.workspace_session_service import (
    resolve_workspace_session_for_access,
)

# Starlette WebSocketState values: 0=CONNECTING 1=CONNECTED 2=DISCONNECTED
_WS_STATE_DISCONNECTED = 2

_logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workspaces", tags=["workspace-terminal"])

_CLOSE_POLICY_VIOLATION = 4001
_CLOSE_GOING_AWAY = 1001
_CLOSE_INTERNAL_ERROR = 1011


@router.websocket("/{workspace_id}/terminal")
async def workspace_terminal(
    workspace_id: int,
    websocket: WebSocket,
    token: str = Query(..., description="Workspace session token (from POST /workspaces/attach)"),
    db: Session = Depends(get_db),
) -> None:
    """Interactive PTY terminal inside the workspace container.

    Accepts a workspace session token as a query parameter (``?token=...``).
    The connection is authenticated and the workspace must be in RUNNING state.

    Protocol:
    - **Client → Server**: binary frames → stdin; text JSON ``{"type":"resize","cols":N,"rows":N}``
    - **Server → Client**: binary frames → stdout/stderr

    The session must have been established via ``POST /workspaces/{id}/attach``.
    """
    # Decode JWT to get user_id (we need the auth user to validate the workspace session).
    try:
        user_id = decode_access_user_id(token)
        user = get_user_auth_entry(db, user_id=user_id)
    except Exception:
        # token may be a workspace session token, not a JWT — try workspace session path
        user = None
        user_id = None

    # Workspace session validation (preferred path for terminals — uses workspace session token).
    # We support two auth modes:
    # 1. JWT token (user is identified from JWT) + workspace ownership check.
    # 2. Workspace session token (dnws_...) — validated against the session table.
    ws_obj = db.get(Workspace, workspace_id)
    if ws_obj is None:
        await websocket.close(code=_CLOSE_POLICY_VIOLATION, reason="workspace_not_found")
        return

    # Feature gate: terminal must be explicitly enabled in the workspace config.
    _latest_cfg = db.exec(
        select(WorkspaceConfig)
        .where(WorkspaceConfig.workspace_id == workspace_id)
        .order_by(WorkspaceConfig.version.desc())
    ).first()
    _features = get_workspace_features(_latest_cfg.config_json if _latest_cfg else None)
    if not _features.terminal_enabled:
        await websocket.close(
            code=_CLOSE_POLICY_VIOLATION,
            reason="terminal_feature_not_enabled",
        )
        _logger.info(
            "terminal_rejected_feature_disabled",
            extra={"workspace_id": workspace_id},
        )
        return

    if user is not None:
        # JWT auth path: verify ownership.
        if ws_obj.owner_user_id != user.user_auth_id:
            await websocket.close(code=_CLOSE_POLICY_VIOLATION, reason="access_denied")
            return
        resolved_user_id = int(user.user_auth_id)
    else:
        # Workspace session token path.
        try:
            session_row = resolve_workspace_session_for_access(
                db,
                workspace_id=workspace_id,
                user_id=ws_obj.owner_user_id,
                token_plain=token,
            )
            resolved_user_id = int(session_row.user_id)
        except WorkspaceAccessDeniedError as exc:
            await websocket.close(code=_CLOSE_POLICY_VIOLATION, reason=str(exc))
            return

    # Verify workspace RUNNING state.
    if ws_obj.status != WorkspaceStatus.RUNNING.value:
        await websocket.close(
            code=_CLOSE_GOING_AWAY,
            reason=f"workspace_not_running (status={ws_obj.status})",
        )
        return

    runtime = db.exec(
        select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id)
    ).first()
    if runtime is None or not runtime.container_id:
        await websocket.close(code=_CLOSE_GOING_AWAY, reason="container_not_ready")
        return

    settings = get_settings()
    shell = settings.devnest_workspace_shell or "/bin/bash"
    cols = settings.devnest_terminal_default_cols or 200
    rows = settings.devnest_terminal_default_rows or 50

    try:
        bundle = resolve_node_execution_bundle(db, runtime.node_id)
    except Exception as exc:
        _logger.warning("terminal_bundle_resolve_failed", extra={"workspace_id": workspace_id, "error": str(exc)})
        await websocket.close(code=_CLOSE_INTERNAL_ERROR, reason="bundle_resolve_failed")
        return

    _logger.info(
        "terminal_connect",
        extra={"workspace_id": workspace_id, "user_id": resolved_user_id, "container_id": runtime.container_id},
    )
    record_audit(
        db,
        action=AuditAction.INTEGRATION_TERMINAL_SESSION_STARTED.value,
        resource_type="workspace",
        resource_id=workspace_id,
        actor_user_id=resolved_user_id,
        actor_type=AuditActorType.USER.value,
        outcome=AuditOutcome.SUCCESS.value,
        workspace_id=workspace_id,
        metadata={"container_id": runtime.container_id, "shell": shell},
    )
    record_usage(
        db,
        event_type=UsageEventType.TERMINAL_SESSION.value,
        workspace_id=workspace_id,
        owner_user_id=resolved_user_id,
    )
    db.commit()

    try:
        await relay_terminal(
            websocket,
            bundle,
            runtime.container_id,
            shell=shell,
            cols=cols,
            rows=rows,
        )
    except TerminalError as exc:
        _logger.warning("terminal_setup_error", extra={"workspace_id": workspace_id, "error": str(exc)})
        if websocket.client_state.value < _WS_STATE_DISCONNECTED:
            try:
                await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))
                await websocket.close(code=_CLOSE_INTERNAL_ERROR)
            except Exception:
                pass
    except WebSocketDisconnect:
        _logger.info("terminal_disconnected", extra={"workspace_id": workspace_id})
    except Exception as exc:
        _logger.error("terminal_relay_error", extra={"workspace_id": workspace_id, "error": str(exc)}, exc_info=True)
        try:
            await websocket.close(code=_CLOSE_INTERNAL_ERROR)
        except Exception:
            pass
