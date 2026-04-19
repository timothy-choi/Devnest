"""Internal ForwardAuth endpoint for Traefik gateway session/auth enforcement.

Traefik calls ``GET /internal/gateway/auth`` before proxying each workspace request.
The backend validates:
  - workspace_id (derived from X-Forwarded-Host matching ``ws-{id}.<base_domain>``)
  - session token (X-DevNest-Workspace-Session header forwarded from the client)
  - workspace exists and is RUNNING
  - session is ACTIVE and not expired

Returns:
  200 OK   — Traefik forwards the request to the workspace upstream.
  401      — Traefik responds with 401 Unauthorized to the client.

ForwardAuth copies request headers verbatim; this handler must never expose secrets in its
response headers. It runs in a stateless, read-only path (no session side effects on deny).

TODO: When DEVNEST_GATEWAY_AUTH_ENABLED=false (dev/local) this endpoint returns 200
      unconditionally so local stacks can operate without session setup overhead.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re

from fastapi import APIRouter, Cookie, Depends, Header, Request
from fastapi.responses import Response
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.libs.db.database import get_db
from app.libs.observability.log_events import LogEvent, log_event
from app.services.workspace_service.models import Workspace, WorkspaceSession
from app.services.workspace_service.models.enums import WorkspaceSessionStatus, WorkspaceStatus
from app.services.workspace_service.services.workspace_session_service import (
    WORKSPACE_SESSION_COOKIE_NAME,
    WORKSPACE_SESSION_HTTP_HEADER,
    hash_workspace_session_token,
)

_logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/gateway",
    tags=["internal-gateway"],
)

# Primary patterns:
#   ws-{integer}.{base_domain}
#   ws-{integer}-{storage_key}.{base_domain}
_WS_HOST_RE = re.compile(r"^ws-(\d+)(?:-[a-z0-9-]+)?\.")


def _correlation_id_from_request(request: Request) -> str | None:
    return getattr(request.state, "correlation_id", None)


def _workspace_id_from_host(host: str, base_domain: str) -> int | None:
    """Extract workspace integer id from hostnames like ``ws-42.app.devnest.local``.

    Also accepts unique per-workspace hosts like ``ws-42-deadbeef.app.devnest.local`` and legacy
    ``{id}.{base_domain}`` (without the ``ws-`` prefix) for older route-admin registrations so
    ForwardAuth and Traefik stay aligned when hosts are migrated.
    """
    host_clean = (host or "").strip().split(":")[0].lower()
    m = _WS_HOST_RE.match(host_clean)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    dom = (base_domain or "").strip().lower().strip(".")
    if not dom:
        return None
    suffix = f".{dom}"
    if not host_clean.endswith(suffix):
        return None
    prefix = host_clean[: -len(suffix)]
    if prefix.isdigit():
        try:
            return int(prefix)
        except ValueError:
            return None
    return None


@router.get(
    "/auth",
    summary="ForwardAuth workspace session check (Traefik edge)",
    response_class=Response,
    status_code=200,
    include_in_schema=False,  # internal; not part of the public OpenAPI spec
)
def gateway_forward_auth(
    request: Request,
    session: Session = Depends(get_db),
    x_forwarded_host: str | None = Header(default=None, alias="X-Forwarded-Host"),
    x_devnest_ws_session: str | None = Header(default=None, alias=WORKSPACE_SESSION_HTTP_HEADER),
    devnest_ws_session_cookie: str | None = Cookie(default=None, alias=WORKSPACE_SESSION_COOKIE_NAME),
) -> Response:
    """
    Validate the workspace session for Traefik ForwardAuth.

    Traefik injects ``X-Forwarded-Host`` with the original host; the client provides
    ``X-DevNest-Workspace-Session`` as a bearer-style workspace token.
    """
    settings = get_settings()
    cid = _correlation_id_from_request(request)

    # Local/dev bypass: operator sets DEVNEST_GATEWAY_AUTH_ENABLED=false to skip session
    # validation while developing without session tokens.
    if not settings.devnest_gateway_auth_enabled:
        return Response(status_code=200)

    # Derive workspace_id from the forwarded host header.
    forwarded_host = (x_forwarded_host or request.headers.get("host", "")).strip()
    workspace_id = _workspace_id_from_host(forwarded_host, settings.devnest_base_domain)
    if workspace_id is None:
        log_event(
            _logger,
            LogEvent.GATEWAY_AUTH_DENIED,
            correlation_id=cid,
            reason="host_no_workspace_id",
            forwarded_host=forwarded_host,
        )
        return Response(status_code=401, content="workspace host not recognized")

    # Session token: header (API clients) or HttpOnly cookie (browser navigations to the gateway).
    token_plain = (x_devnest_ws_session or "").strip()
    if not token_plain:
        token_plain = (devnest_ws_session_cookie or "").strip()
    if not token_plain:
        log_event(
            _logger,
            LogEvent.GATEWAY_AUTH_DENIED,
            correlation_id=cid,
            workspace_id=workspace_id,
            reason="missing_session_token",
        )
        return Response(
            status_code=401,
            content=(
                "workspace session required: send X-DevNest-Workspace-Session or attach cookie "
                f"(HttpOnly {WORKSPACE_SESSION_COOKIE_NAME} from POST /workspaces/attach/{{id}})"
            ),
        )

    # Look up session by token hash.
    token_hash = hash_workspace_session_token(token_plain)
    ws_session = session.exec(
        select(WorkspaceSession).where(WorkspaceSession.session_token_hash == token_hash),
    ).first()

    if ws_session is None:
        log_event(
            _logger,
            LogEvent.GATEWAY_AUTH_DENIED,
            correlation_id=cid,
            workspace_id=workspace_id,
            reason="unknown_token",
        )
        return Response(status_code=401, content="invalid workspace session token")

    # Validate workspace binding.
    if int(ws_session.workspace_id) != int(workspace_id):
        log_event(
            _logger,
            LogEvent.GATEWAY_AUTH_DENIED,
            correlation_id=cid,
            workspace_id=workspace_id,
            workspace_session_id=ws_session.workspace_session_id,
            reason="workspace_mismatch",
        )
        return Response(status_code=401, content="session workspace mismatch")

    # Validate session status.
    if ws_session.status != WorkspaceSessionStatus.ACTIVE.value:
        log_event(
            _logger,
            LogEvent.GATEWAY_AUTH_DENIED,
            correlation_id=cid,
            workspace_id=workspace_id,
            workspace_session_id=ws_session.workspace_session_id,
            reason=f"session_status_{ws_session.status}",
        )
        return Response(status_code=401, content="workspace session is not active")

    # Validate expiry (read-only; lazy expiry write is handled by the access endpoint).
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    exp = ws_session.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp <= now:
        log_event(
            _logger,
            LogEvent.GATEWAY_AUTH_DENIED,
            correlation_id=cid,
            workspace_id=workspace_id,
            workspace_session_id=ws_session.workspace_session_id,
            reason="session_expired",
        )
        return Response(status_code=401, content="workspace session expired")

    # Validate workspace is RUNNING.
    workspace = session.get(Workspace, workspace_id)
    if workspace is None:
        log_event(
            _logger,
            LogEvent.GATEWAY_AUTH_DENIED,
            correlation_id=cid,
            workspace_id=workspace_id,
            reason="workspace_not_found",
        )
        return Response(status_code=401, content="workspace not found")

    if workspace.status != WorkspaceStatus.RUNNING.value:
        log_event(
            _logger,
            LogEvent.GATEWAY_AUTH_DENIED,
            correlation_id=cid,
            workspace_id=workspace_id,
            reason=f"workspace_status_{workspace.status}",
        )
        return Response(status_code=401, content=f"workspace is not running ({workspace.status})")

    log_event(
        _logger,
        LogEvent.GATEWAY_AUTH_ALLOWED,
        correlation_id=cid,
        workspace_id=workspace_id,
        workspace_session_id=ws_session.workspace_session_id,
    )
    return Response(status_code=200)
