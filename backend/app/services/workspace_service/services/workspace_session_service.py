"""Workspace session issuance, verification, and reconciliation with ``Workspace.active_sessions_count``."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.libs.observability.log_events import LogEvent, log_event

from ..errors import WorkspaceAccessDeniedError
from ..models import Workspace, WorkspaceSession
from ..models.enums import WorkspaceSessionRole, WorkspaceSessionStatus

logger = logging.getLogger(__name__)

WORKSPACE_SESSION_TOKEN_PREFIX = "dnws_"

# HTTP header clients send on GET /workspaces/{id}/access (after POST /attach).
WORKSPACE_SESSION_HTTP_HEADER = "X-DevNest-Workspace-Session"
# HttpOnly cookie (optional): browsers cannot set custom headers on top-level navigations to the
# workspace gateway host; Traefik ForwardAuth reads this cookie when gateway auth is enabled.
WORKSPACE_SESSION_COOKIE_NAME = "devnest_ws_session"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc_aware(dt: datetime) -> datetime:
    """SQLite often yields naive datetimes (stored as UTC); PostgreSQL returns aware."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def generate_workspace_session_token() -> str:
    """Opaque bearer segment; store only :func:`hash_workspace_session_token` in the database."""
    return WORKSPACE_SESSION_TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_workspace_session_token(token: str) -> str:
    """Keyed hash so a DB leak alone does not allow offline guessing of raw tokens."""
    key = get_settings().jwt_secret_key.encode("utf-8")
    return hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()


def count_effective_active_sessions(session: Session, workspace_id: int, *, now: datetime | None = None) -> int:
    ts = now or _now()
    rows = session.exec(
        select(WorkspaceSession).where(
            WorkspaceSession.workspace_id == workspace_id,
            WorkspaceSession.status == WorkspaceSessionStatus.ACTIVE.value,
        ),
    ).all()
    return sum(1 for r in rows if _as_utc_aware(r.expires_at) > ts)


def reconcile_workspace_active_session_count(session: Session, workspace_id: int, *, now: datetime | None = None) -> int:
    """Set ``Workspace.active_sessions_count`` from non-expired ACTIVE session rows."""
    ws = session.get(Workspace, workspace_id)
    if ws is None:
        return 0
    cnt = count_effective_active_sessions(session, workspace_id, now=now)
    ws.active_sessions_count = cnt
    ws.updated_at = now or _now()
    session.add(ws)
    return cnt


def create_workspace_session(
    session: Session,
    *,
    workspace_id: int,
    user_id: int,
    client_metadata: dict | None,
    correlation_id: str | None = None,
) -> tuple[str, WorkspaceSession]:
    """Persist a new ACTIVE session; returns ``(plain_token, row)`` — plain token is shown to the client once."""
    now = _now()
    ttl = max(60, int(get_settings().workspace_session_ttl_seconds))
    token = generate_workspace_session_token()
    token_hash = hash_workspace_session_token(token)
    meta = dict(client_metadata or {})
    row = WorkspaceSession(
        workspace_id=workspace_id,
        user_id=user_id,
        role=WorkspaceSessionRole.OWNER.value,
        status=WorkspaceSessionStatus.ACTIVE.value,
        session_token_hash=token_hash,
        issued_at=now,
        expires_at=now + timedelta(seconds=ttl),
        last_seen_at=now,
        client_metadata=meta,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    reconcile_workspace_active_session_count(session, workspace_id, now=now)
    assert row.workspace_session_id is not None
    log_event(
        logger,
        LogEvent.WORKSPACE_SESSION_CREATED,
        correlation_id=correlation_id,
        workspace_id=workspace_id,
        user_id=user_id,
        workspace_session_id=row.workspace_session_id,
    )
    return token, row


def resolve_workspace_session_for_access(
    session: Session,
    *,
    workspace_id: int,
    user_id: int,
    token_plain: str,
    correlation_id: str | None = None,
) -> WorkspaceSession:
    """
    Validate token, workspace, and user; lazy-expire; refresh ``last_seen_at``.

    Raises:
        WorkspaceAccessDeniedError: missing binding, wrong user/workspace, revoked, or expired.
    """
    raw = (token_plain or "").strip()
    if not raw:
        log_event(
            logger,
            LogEvent.WORKSPACE_ACCESS_DENIED,
            correlation_id=correlation_id,
            workspace_id=workspace_id,
            user_id=user_id,
            reason="missing_token",
        )
        raise WorkspaceAccessDeniedError("Workspace session token required (use POST /workspaces/attach/{id} first).")

    th = hash_workspace_session_token(raw)
    row = session.exec(select(WorkspaceSession).where(WorkspaceSession.session_token_hash == th)).first()
    if row is None:
        log_event(
            logger,
            LogEvent.WORKSPACE_ACCESS_DENIED,
            correlation_id=correlation_id,
            workspace_id=workspace_id,
            user_id=user_id,
            reason="unknown_token",
        )
        raise WorkspaceAccessDeniedError("Invalid workspace session token.")

    if row.workspace_id != workspace_id or row.user_id != user_id:
        log_event(
            logger,
            LogEvent.WORKSPACE_ACCESS_DENIED,
            correlation_id=correlation_id,
            workspace_id=workspace_id,
            user_id=user_id,
            reason="token_workspace_mismatch",
        )
        raise WorkspaceAccessDeniedError("Invalid workspace session token.")

    now = _now()
    if row.status != WorkspaceSessionStatus.ACTIVE.value:
        log_event(
            logger,
            LogEvent.WORKSPACE_ACCESS_DENIED,
            correlation_id=correlation_id,
            workspace_id=workspace_id,
            user_id=user_id,
            reason=f"status_{row.status}",
        )
        raise WorkspaceAccessDeniedError("Workspace session is no longer active.")

    if _as_utc_aware(row.expires_at) <= now:
        row.status = WorkspaceSessionStatus.EXPIRED.value
        row.updated_at = now
        session.add(row)
        reconcile_workspace_active_session_count(session, workspace_id, now=now)
        session.commit()
        log_event(
            logger,
            LogEvent.WORKSPACE_SESSION_EXPIRED,
            correlation_id=correlation_id,
            workspace_id=workspace_id,
            user_id=user_id,
            workspace_session_id=row.workspace_session_id,
        )
        log_event(
            logger,
            LogEvent.WORKSPACE_ACCESS_DENIED,
            correlation_id=correlation_id,
            workspace_id=workspace_id,
            user_id=user_id,
            reason="expired",
        )
        raise WorkspaceAccessDeniedError("Workspace session expired; attach again.")

    row.last_seen_at = now
    row.updated_at = now
    session.add(row)
    session.flush()
    log_event(
        logger,
        LogEvent.WORKSPACE_SESSION_REFRESHED,
        level=logging.DEBUG,
        correlation_id=correlation_id,
        workspace_id=workspace_id,
        user_id=user_id,
        workspace_session_id=row.workspace_session_id,
    )
    log_event(
        logger,
        LogEvent.WORKSPACE_ACCESS_GRANTED,
        level=logging.DEBUG,
        correlation_id=correlation_id,
        workspace_id=workspace_id,
        user_id=user_id,
        workspace_session_id=row.workspace_session_id,
    )
    return row


def revoke_all_workspace_sessions(
    session: Session,
    workspace_id: int,
    *,
    reason: str,
    correlation_id: str | None = None,
) -> int:
    """Mark all ACTIVE sessions REVOKED (stop/delete/restart/update success paths)."""
    now = _now()
    rows = session.exec(
        select(WorkspaceSession).where(
            WorkspaceSession.workspace_id == workspace_id,
            WorkspaceSession.status == WorkspaceSessionStatus.ACTIVE.value,
        ),
    ).all()
    n = 0
    for row in rows:
        row.status = WorkspaceSessionStatus.REVOKED.value
        row.updated_at = now
        session.add(row)
        n += 1
    if n:
        reconcile_workspace_active_session_count(session, workspace_id, now=now)
        log_event(
            logger,
            LogEvent.WORKSPACE_SESSION_REVOKED_BULK,
            correlation_id=correlation_id,
            workspace_id=workspace_id,
            revoked_count=n,
            reason=reason,
        )
    return n
