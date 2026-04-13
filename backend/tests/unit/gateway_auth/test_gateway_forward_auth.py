"""Unit tests: GET /internal/gateway/auth ForwardAuth endpoint.

All tests use an in-memory SQLite engine + FastAPI TestClient; no PostgreSQL required.

Scenarios:
  1. Auth disabled globally → always 200.
  2. Valid session + RUNNING workspace → 200.
  3. Missing session token → 401.
  4. Unknown token → 401.
  5. Session workspace mismatch → 401.
  6. Session not ACTIVE (REVOKED) → 401.
  7. Session expired → 401.
  8. Workspace not RUNNING → 401.
  9. Host does not match ws-{id}.{domain} pattern → 401.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.libs.db.database import get_db
from app.services.auth_service.models import UserAuth
from app.services.workspace_service.api.routers.internal_gateway_auth import router
from app.services.workspace_service.models import Workspace, WorkspaceSession
from app.services.workspace_service.models.enums import (
    WorkspaceSessionStatus,
    WorkspaceStatus,
)
from app.services.workspace_service.services.workspace_session_service import (
    WORKSPACE_SESSION_HTTP_HEADER,
    generate_workspace_session_token,
    hash_workspace_session_token,
)

_NOW = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
_FUTURE = _NOW + timedelta(hours=24)
_PAST = datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

WS_ID = 42
USER_ID = 1
BASE_DOMAIN = "app.devnest.local"
WS_HOST = f"ws-{WS_ID}.{BASE_DOMAIN}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(e)
    return e


def _make_app(engine):
    app = FastAPI()
    app.include_router(router)

    def _db():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db] = _db
    return app


def _seed_user(session: Session) -> int:
    # Use a unique email/username per call to avoid conflicts across tests sharing the same engine.
    import uuid as _uuid
    uid = str(_uuid.uuid4())[:8]
    u = UserAuth(username=f"tester-{uid}", email=f"tester-{uid}@devnest.local", password_hash="x")
    session.add(u)
    session.flush()
    assert u.user_auth_id is not None
    return u.user_auth_id


def _seed_workspace(session: Session, owner_id: int, status: str = WorkspaceStatus.RUNNING.value) -> int:
    ws = Workspace(
        name="test-ws",
        owner_user_id=owner_id,
        status=status,
        is_private=True,
        active_sessions_count=0,
        created_at=_NOW,
        updated_at=_NOW,
    )
    session.add(ws)
    session.flush()
    assert ws.workspace_id is not None
    return ws.workspace_id


def _seed_session(
    session: Session,
    workspace_id: int,
    user_id: int,
    *,
    status: str = WorkspaceSessionStatus.ACTIVE.value,
    expires_at: datetime | None = None,
) -> str:
    token_plain = generate_workspace_session_token()
    token_hash = hash_workspace_session_token(token_plain)
    ws_session = WorkspaceSession(
        workspace_id=workspace_id,
        user_id=user_id,
        status=status,
        session_token_hash=token_hash,
        issued_at=_NOW,
        expires_at=expires_at or _FUTURE,
        last_seen_at=_NOW,
        client_metadata={},
        updated_at=_NOW,
    )
    session.add(ws_session)
    session.commit()
    return token_plain


def _settings_auth_enabled(**extra):
    """Return a mock Settings-like object with auth enabled and test base domain."""
    from unittest.mock import MagicMock

    s = MagicMock()
    s.devnest_gateway_auth_enabled = True
    s.devnest_base_domain = BASE_DOMAIN
    for k, v in extra.items():
        setattr(s, k, v)
    return s


def _settings_auth_disabled():
    from unittest.mock import MagicMock

    s = MagicMock()
    s.devnest_gateway_auth_enabled = False
    s.devnest_base_domain = BASE_DOMAIN
    return s


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _seed_data(engine, *, ws_status: str = WorkspaceStatus.RUNNING.value, session_status: str = WorkspaceSessionStatus.ACTIVE.value, expires_at=None):
    """Seed user, workspace, and session; return (ws_id, token)."""
    with Session(engine) as s:
        uid = _seed_user(s)
        ws_id = _seed_workspace(s, uid, status=ws_status)
        s.commit()
        token = _seed_session(s, ws_id, uid, status=session_status, expires_at=expires_at)
        return ws_id, token


def test_auth_disabled_always_allows(engine):
    """When DEVNEST_GATEWAY_AUTH_ENABLED=false, endpoint returns 200 regardless."""
    with patch(
        "app.services.workspace_service.api.routers.internal_gateway_auth.get_settings",
        return_value=_settings_auth_disabled(),
    ):
        client = TestClient(_make_app(engine))
        resp = client.get("/internal/gateway/auth")
        assert resp.status_code == 200


def test_valid_session_running_workspace_allows(engine):
    """Valid session + RUNNING workspace → 200."""
    ws_id, token = _seed_data(engine, ws_status=WorkspaceStatus.RUNNING.value)
    host = f"ws-{ws_id}.{BASE_DOMAIN}"

    with patch(
        "app.services.workspace_service.api.routers.internal_gateway_auth.get_settings",
        return_value=_settings_auth_enabled(),
    ):
        client = TestClient(_make_app(engine))
        resp = client.get(
            "/internal/gateway/auth",
            headers={
                "X-Forwarded-Host": host,
                WORKSPACE_SESSION_HTTP_HEADER: token,
            },
        )
        assert resp.status_code == 200, f"body={resp.text!r}"


def test_missing_session_token_denied(engine):
    """No session header → 401."""
    with Session(engine) as s:
        uid = _seed_user(s)
        ws_id = _seed_workspace(s, uid)
        s.commit()
    host = f"ws-{ws_id}.{BASE_DOMAIN}"

    with patch(
        "app.services.workspace_service.api.routers.internal_gateway_auth.get_settings",
        return_value=_settings_auth_enabled(),
    ):
        client = TestClient(_make_app(engine))
        resp = client.get("/internal/gateway/auth", headers={"X-Forwarded-Host": host})
        assert resp.status_code == 401


def test_unknown_token_denied(engine):
    """Token not in DB → 401."""
    with Session(engine) as s:
        uid = _seed_user(s)
        ws_id = _seed_workspace(s, uid)
        s.commit()
    host = f"ws-{ws_id}.{BASE_DOMAIN}"

    with patch(
        "app.services.workspace_service.api.routers.internal_gateway_auth.get_settings",
        return_value=_settings_auth_enabled(),
    ):
        client = TestClient(_make_app(engine))
        resp = client.get(
            "/internal/gateway/auth",
            headers={
                "X-Forwarded-Host": host,
                WORKSPACE_SESSION_HTTP_HEADER: "dnws_fake_token_not_in_db",
            },
        )
        assert resp.status_code == 401


def test_session_workspace_mismatch_denied(engine):
    """Session belongs to a different workspace → 401."""
    with Session(engine) as s:
        uid = _seed_user(s)
        ws_id_1 = _seed_workspace(s, uid)
        ws_id_2 = _seed_workspace(s, uid)
        s.commit()
        token = _seed_session(s, ws_id_1, uid)
    host = f"ws-{ws_id_2}.{BASE_DOMAIN}"

    with patch(
        "app.services.workspace_service.api.routers.internal_gateway_auth.get_settings",
        return_value=_settings_auth_enabled(),
    ):
        client = TestClient(_make_app(engine))
        resp = client.get(
            "/internal/gateway/auth",
            headers={
                "X-Forwarded-Host": host,
                WORKSPACE_SESSION_HTTP_HEADER: token,
            },
        )
        assert resp.status_code == 401


def test_revoked_session_denied(engine):
    """REVOKED session → 401."""
    ws_id, token = _seed_data(engine, session_status=WorkspaceSessionStatus.REVOKED.value)
    host = f"ws-{ws_id}.{BASE_DOMAIN}"

    with patch(
        "app.services.workspace_service.api.routers.internal_gateway_auth.get_settings",
        return_value=_settings_auth_enabled(),
    ):
        client = TestClient(_make_app(engine))
        resp = client.get(
            "/internal/gateway/auth",
            headers={
                "X-Forwarded-Host": host,
                WORKSPACE_SESSION_HTTP_HEADER: token,
            },
        )
        assert resp.status_code == 401


def test_expired_session_denied(engine):
    """Expired session → 401."""
    ws_id, token = _seed_data(engine, expires_at=_PAST)
    host = f"ws-{ws_id}.{BASE_DOMAIN}"

    with patch(
        "app.services.workspace_service.api.routers.internal_gateway_auth.get_settings",
        return_value=_settings_auth_enabled(),
    ):
        client = TestClient(_make_app(engine))
        resp = client.get(
            "/internal/gateway/auth",
            headers={
                "X-Forwarded-Host": host,
                WORKSPACE_SESSION_HTTP_HEADER: token,
            },
        )
        assert resp.status_code == 401


def test_workspace_not_running_denied(engine):
    """Valid session but workspace STOPPED → 401."""
    ws_id, token = _seed_data(engine, ws_status=WorkspaceStatus.STOPPED.value)
    host = f"ws-{ws_id}.{BASE_DOMAIN}"

    with patch(
        "app.services.workspace_service.api.routers.internal_gateway_auth.get_settings",
        return_value=_settings_auth_enabled(),
    ):
        client = TestClient(_make_app(engine))
        resp = client.get(
            "/internal/gateway/auth",
            headers={
                "X-Forwarded-Host": host,
                WORKSPACE_SESSION_HTTP_HEADER: token,
            },
        )
        assert resp.status_code == 401


def test_non_workspace_host_denied(engine):
    """Host that does not match ws-{id}.{domain} pattern → 401."""
    with patch(
        "app.services.workspace_service.api.routers.internal_gateway_auth.get_settings",
        return_value=_settings_auth_enabled(),
    ):
        client = TestClient(_make_app(engine))
        resp = client.get(
            "/internal/gateway/auth",
            headers={
                "X-Forwarded-Host": "whoami.app.devnest.local",
                WORKSPACE_SESSION_HTTP_HEADER: "dnws_some_token",
            },
        )
        assert resp.status_code == 401
