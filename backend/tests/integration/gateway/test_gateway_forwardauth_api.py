"""Integration tests for the ForwardAuth endpoint (GET /internal/gateway/auth).

Traefik calls this endpoint before proxying each workspace request. These tests verify:

- Valid workspace session → 200
- Expired session → 401
- Nonexistent workspace → 401 (session exists but workspace row is missing)
- Auth bypass mode (DEVNEST_GATEWAY_AUTH_ENABLED=false) → 200 unconditionally

All tests run against a real PostgreSQL database. No Docker or real gateway required.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import status
from sqlmodel import Session

from app.libs.common.config import get_settings
from app.services.auth_service.models import UserAuth
from app.services.workspace_service.models import Workspace, WorkspaceRuntime, WorkspaceSession
from app.services.workspace_service.models.enums import (
    WorkspaceRuntimeHealthStatus,
    WorkspaceSessionRole,
    WorkspaceSessionStatus,
    WorkspaceStatus,
)
from app.services.workspace_service.services.workspace_session_service import (
    WORKSPACE_SESSION_HTTP_HEADER,
    generate_workspace_session_token,
    hash_workspace_session_token,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_DOMAIN = "app.devnest.local"


def _ws_forwarded_host(workspace_id: int, base_domain: str = _BASE_DOMAIN) -> str:
    return f"ws-{workspace_id}.{base_domain}"


def _seed_user(db_session: Session) -> int:
    """Insert a UserAuth row and return its id."""
    user = UserAuth(
        username=f"gw_u_{uuid.uuid4().hex[:8]}",
        email=f"gw_{uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    assert user.user_auth_id is not None
    return user.user_auth_id


def _seed_running_workspace(db_session: Session, *, owner_user_id: int | None = None) -> Workspace:
    if owner_user_id is None:
        owner_user_id = _seed_user(db_session)
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name=f"gw-auth-ws-{uuid.uuid4().hex[:8]}",
        owner_user_id=owner_user_id,
        status=WorkspaceStatus.RUNNING.value,
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)
    return ws


def _seed_active_session(
    db_session: Session,
    *,
    workspace_id: int,
    user_id: int | None = None,
    ttl_seconds: int = 3600,
) -> tuple[str, WorkspaceSession]:
    """Create an ACTIVE session row; return (plain_token, session_row)."""
    if user_id is None:
        user_id = _seed_user(db_session)
    plain_token = generate_workspace_session_token()
    token_hash = hash_workspace_session_token(plain_token)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl_seconds)
    sess = WorkspaceSession(
        workspace_id=workspace_id,
        user_id=user_id,
        session_token_hash=token_hash,
        status=WorkspaceSessionStatus.ACTIVE.value,
        role=WorkspaceSessionRole.OWNER.value,
        created_at=now,
        updated_at=now,
        expires_at=expires_at,
    )
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    return plain_token, sess


def _seed_expired_session(
    db_session: Session,
    *,
    workspace_id: int,
    user_id: int | None = None,
) -> tuple[str, WorkspaceSession]:
    """Create an ACTIVE session that has already expired."""
    return _seed_active_session(
        db_session,
        workspace_id=workspace_id,
        user_id=user_id,
        ttl_seconds=-1,  # expires in the past
    )


def _forwardauth_headers(
    plain_token: str | None = None,
    *,
    forwarded_host: str | None = None,
) -> dict[str, str]:
    h: dict[str, str] = {}
    if forwarded_host is not None:
        h["X-Forwarded-Host"] = forwarded_host
    if plain_token is not None:
        h[WORKSPACE_SESSION_HTTP_HEADER] = plain_token
    return h


# ---------------------------------------------------------------------------
# Auth bypass mode (DEVNEST_GATEWAY_AUTH_ENABLED=false — default in tests)
# ---------------------------------------------------------------------------


class TestForwardAuthBypassMode:
    def test_bypass_mode_returns_200_without_any_headers(self, client, monkeypatch) -> None:
        monkeypatch.setenv("DEVNEST_GATEWAY_AUTH_ENABLED", "false")
        get_settings.cache_clear()

        r = client.get("/internal/gateway/auth")
        assert r.status_code == status.HTTP_200_OK

    def test_bypass_mode_returns_200_with_invalid_token(self, client, monkeypatch) -> None:
        monkeypatch.setenv("DEVNEST_GATEWAY_AUTH_ENABLED", "false")
        get_settings.cache_clear()

        r = client.get(
            "/internal/gateway/auth",
            headers=_forwardauth_headers(
                plain_token="totally-invalid-token",
                forwarded_host="ws-9999.app.devnest.local",
            ),
        )
        assert r.status_code == status.HTTP_200_OK

    def test_bypass_mode_returns_200_regardless_of_workspace_existence(
        self, client, monkeypatch
    ) -> None:
        monkeypatch.setenv("DEVNEST_GATEWAY_AUTH_ENABLED", "false")
        get_settings.cache_clear()

        r = client.get(
            "/internal/gateway/auth",
            headers={"X-Forwarded-Host": "ws-99999.app.devnest.local"},
        )
        assert r.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# Auth enforcement mode (DEVNEST_GATEWAY_AUTH_ENABLED=true)
# ---------------------------------------------------------------------------


class TestForwardAuthEnforcementMode:
    @pytest.fixture(autouse=True)
    def _enable_gateway_auth(self, monkeypatch) -> None:
        monkeypatch.setenv("DEVNEST_GATEWAY_AUTH_ENABLED", "true")
        monkeypatch.setenv("DEVNEST_BASE_DOMAIN", _BASE_DOMAIN)
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    # ------------------------------------------------------------------
    # Happy path: valid session → 200
    # ------------------------------------------------------------------

    def test_valid_session_returns_200(self, client, db_session: Session) -> None:
        ws = _seed_running_workspace(db_session)
        plain_token, _ = _seed_active_session(db_session, workspace_id=ws.workspace_id)

        r = client.get(
            "/internal/gateway/auth",
            headers=_forwardauth_headers(
                plain_token=plain_token,
                forwarded_host=_ws_forwarded_host(ws.workspace_id),
            ),
        )
        assert r.status_code == status.HTTP_200_OK

    def test_valid_session_with_port_in_forwarded_host_returns_200(
        self, client, db_session: Session
    ) -> None:
        """X-Forwarded-Host may include port; parser strips it."""
        ws = _seed_running_workspace(db_session)
        plain_token, _ = _seed_active_session(db_session, workspace_id=ws.workspace_id)

        r = client.get(
            "/internal/gateway/auth",
            headers=_forwardauth_headers(
                plain_token=plain_token,
                forwarded_host=f"ws-{ws.workspace_id}.{_BASE_DOMAIN}:443",
            ),
        )
        assert r.status_code == status.HTTP_200_OK

    # ------------------------------------------------------------------
    # Missing / invalid session token → 401
    # ------------------------------------------------------------------

    def test_missing_session_token_returns_401(self, client, db_session: Session) -> None:
        ws = _seed_running_workspace(db_session)

        r = client.get(
            "/internal/gateway/auth",
            headers={"X-Forwarded-Host": _ws_forwarded_host(ws.workspace_id)},
        )
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalid_session_token_returns_401(self, client, db_session: Session) -> None:
        ws = _seed_running_workspace(db_session)

        r = client.get(
            "/internal/gateway/auth",
            headers=_forwardauth_headers(
                plain_token="invalid-token-does-not-exist",
                forwarded_host=_ws_forwarded_host(ws.workspace_id),
            ),
        )
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    # ------------------------------------------------------------------
    # Expired session → 401
    # ------------------------------------------------------------------

    def test_expired_session_returns_401(self, client, db_session: Session) -> None:
        ws = _seed_running_workspace(db_session)
        plain_token, _ = _seed_expired_session(db_session, workspace_id=ws.workspace_id)

        r = client.get(
            "/internal/gateway/auth",
            headers=_forwardauth_headers(
                plain_token=plain_token,
                forwarded_host=_ws_forwarded_host(ws.workspace_id),
            ),
        )
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    # ------------------------------------------------------------------
    # Unrecognised host → 401
    # ------------------------------------------------------------------

    def test_no_forwarded_host_returns_401(self, client, db_session: Session) -> None:
        ws = _seed_running_workspace(db_session)
        plain_token, _ = _seed_active_session(db_session, workspace_id=ws.workspace_id)

        r = client.get(
            "/internal/gateway/auth",
            headers={WORKSPACE_SESSION_HTTP_HEADER: plain_token},
        )
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    def test_non_workspace_host_returns_401(self, client, db_session: Session) -> None:
        ws = _seed_running_workspace(db_session)
        plain_token, _ = _seed_active_session(db_session, workspace_id=ws.workspace_id)

        r = client.get(
            "/internal/gateway/auth",
            headers=_forwardauth_headers(
                plain_token=plain_token,
                forwarded_host=f"api.{_BASE_DOMAIN}",  # not a ws-{id} host
            ),
        )
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    # ------------------------------------------------------------------
    # Workspace mismatch → 401
    # ------------------------------------------------------------------

    def test_session_workspace_mismatch_returns_401(
        self, client, db_session: Session
    ) -> None:
        """Token belongs to ws A but request is for ws B."""
        ws_a = _seed_running_workspace(db_session)
        ws_b = _seed_running_workspace(db_session)
        plain_token, _ = _seed_active_session(db_session, workspace_id=ws_a.workspace_id)

        r = client.get(
            "/internal/gateway/auth",
            headers=_forwardauth_headers(
                plain_token=plain_token,
                forwarded_host=_ws_forwarded_host(ws_b.workspace_id),  # different workspace
            ),
        )
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    # ------------------------------------------------------------------
    # Workspace not RUNNING → 401
    # ------------------------------------------------------------------

    def test_stopped_workspace_returns_401(self, client, db_session: Session) -> None:
        owner_id = _seed_user(db_session)
        now = datetime.now(timezone.utc)
        ws = Workspace(
            name=f"gw-stopped-{uuid.uuid4().hex[:8]}",
            owner_user_id=owner_id,
            status=WorkspaceStatus.STOPPED.value,
            is_private=True,
            created_at=now,
            updated_at=now,
        )
        db_session.add(ws)
        db_session.commit()
        db_session.refresh(ws)

        plain_token, _ = _seed_active_session(db_session, workspace_id=ws.workspace_id)

        r = client.get(
            "/internal/gateway/auth",
            headers=_forwardauth_headers(
                plain_token=plain_token,
                forwarded_host=_ws_forwarded_host(ws.workspace_id),
            ),
        )
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    def test_deleted_workspace_returns_401(self, client, db_session: Session) -> None:
        owner_id = _seed_user(db_session)
        now = datetime.now(timezone.utc)
        ws = Workspace(
            name=f"gw-deleted-{uuid.uuid4().hex[:8]}",
            owner_user_id=owner_id,
            status=WorkspaceStatus.DELETED.value,
            is_private=True,
            created_at=now,
            updated_at=now,
        )
        db_session.add(ws)
        db_session.commit()
        db_session.refresh(ws)

        plain_token, _ = _seed_active_session(db_session, workspace_id=ws.workspace_id)

        r = client.get(
            "/internal/gateway/auth",
            headers=_forwardauth_headers(
                plain_token=plain_token,
                forwarded_host=_ws_forwarded_host(ws.workspace_id),
            ),
        )
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    # ------------------------------------------------------------------
    # Nonexistent workspace → 401
    # ------------------------------------------------------------------

    def test_nonexistent_workspace_id_in_host_returns_401(
        self, client, db_session: Session
    ) -> None:
        """The X-Forwarded-Host references a workspace_id that doesn't exist in the database."""
        nonexistent_wid = 999_999_998
        # Seed a session with a fake workspace_id so we get past the token-lookup step.
        # We can't use _seed_active_session because it references a real workspace row.
        plain_token = generate_workspace_session_token()
        token_hash = hash_workspace_session_token(plain_token)
        now = datetime.now(timezone.utc)

        # First create a stub workspace row to satisfy the FK, then create the session
        # pointing to a non-existent id by crafting the row directly.
        dummy_owner_id = _seed_user(db_session)
        ws_dummy = Workspace(
            name=f"dummy-{uuid.uuid4().hex[:8]}",
            owner_user_id=dummy_owner_id,
            status=WorkspaceStatus.RUNNING.value,
            is_private=True,
            created_at=now,
            updated_at=now,
        )
        db_session.add(ws_dummy)
        db_session.commit()
        db_session.refresh(ws_dummy)

        sess = WorkspaceSession(
            workspace_id=ws_dummy.workspace_id,
            user_id=dummy_owner_id,
            session_token_hash=token_hash,
            status=WorkspaceSessionStatus.ACTIVE.value,
            role=WorkspaceSessionRole.OWNER.value,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=1),
        )
        db_session.add(sess)
        db_session.commit()

        # Request for a completely different (nonexistent) workspace id in the host
        r = client.get(
            "/internal/gateway/auth",
            headers=_forwardauth_headers(
                plain_token=plain_token,
                forwarded_host=_ws_forwarded_host(nonexistent_wid),
            ),
        )
        # Either workspace_mismatch (401) or workspace_not_found (401)
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    # ------------------------------------------------------------------
    # Revoked / inactive session → 401
    # ------------------------------------------------------------------

    def test_revoked_session_returns_401(self, client, db_session: Session) -> None:
        ws = _seed_running_workspace(db_session)
        plain_token, sess = _seed_active_session(db_session, workspace_id=ws.workspace_id)

        # Revoke the session
        sess.status = WorkspaceSessionStatus.REVOKED.value
        db_session.add(sess)
        db_session.commit()

        r = client.get(
            "/internal/gateway/auth",
            headers=_forwardauth_headers(
                plain_token=plain_token,
                forwarded_host=_ws_forwarded_host(ws.workspace_id),
            ),
        )
        assert r.status_code == status.HTTP_401_UNAUTHORIZED
