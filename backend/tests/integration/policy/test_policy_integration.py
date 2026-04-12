"""Integration tests for the Policy enforcement system (real PostgreSQL, FastAPI client)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.services.auth_service.models import UserAuth
from app.services.policy_service.enums import PolicyType, ScopeType
from app.services.policy_service.models import Policy

INTERNAL_KEY = "integration-test-internal-key"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register_and_login(client: TestClient, email: str, password: str = "securepass1") -> str:
    client.post("/auth/register", json={"username": email.split("@")[0], "email": email, "password": password})
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.json()
    return resp.json()["access_token"]


def _create_workspace(client: TestClient, token: str) -> dict:
    resp = client.post(
        "/workspaces",
        json={"name": "test-ws", "description": "", "runtime": {"image": "nginx:alpine"}, "is_private": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp


def _seed_policy(session: Session, *, rules: dict, scope_type: ScopeType = ScopeType.GLOBAL, scope_id: int | None = None) -> Policy:
    now = datetime.now(timezone.utc)
    p = Policy(
        name=f"test_policy_{datetime.now().timestamp()}",
        policy_type=PolicyType.SYSTEM.value,
        scope_type=scope_type.value,
        scope_id=scope_id,
        rules_json=rules,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(p)
    session.commit()
    return p


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------

class TestPolicyAdminApi:
    def test_create_policy_requires_internal_key(self, client: TestClient) -> None:
        resp = client.post(
            "/internal/policies",
            json={
                "name": "test_pol",
                "policy_type": "system",
                "scope_type": "global",
                "rules": {},
            },
        )
        assert resp.status_code in (401, 503)

    def test_create_and_list_policy(self, client: TestClient) -> None:
        resp = client.post(
            "/internal/policies",
            json={
                "name": "allow_all",
                "policy_type": "system",
                "scope_type": "global",
                "rules": {"allow_workspace_creation": True},
            },
            headers={"X-Internal-API-Key": INTERNAL_KEY},
        )
        assert resp.status_code == 201, resp.json()
        data = resp.json()
        assert data["name"] == "allow_all"
        assert data["is_active"] is True
        assert data["rules_json"]["allow_workspace_creation"] is True

        list_resp = client.get("/internal/policies", headers={"X-Internal-API-Key": INTERNAL_KEY})
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] == 1

    def test_patch_policy_toggles_is_active(self, client: TestClient) -> None:
        create_resp = client.post(
            "/internal/policies",
            json={
                "name": "patch_me",
                "policy_type": "system",
                "scope_type": "global",
                "rules": {},
            },
            headers={"X-Internal-API-Key": INTERNAL_KEY},
        )
        assert create_resp.status_code == 201
        pid = create_resp.json()["policy_id"]

        patch_resp = client.patch(
            f"/internal/policies/{pid}",
            json={"is_active": False},
            headers={"X-Internal-API-Key": INTERNAL_KEY},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["is_active"] is False

    def test_delete_policy(self, client: TestClient) -> None:
        create_resp = client.post(
            "/internal/policies",
            json={"name": "del_me", "policy_type": "system", "scope_type": "global", "rules": {}},
            headers={"X-Internal-API-Key": INTERNAL_KEY},
        )
        assert create_resp.status_code == 201
        pid = create_resp.json()["policy_id"]

        del_resp = client.delete(f"/internal/policies/{pid}", headers={"X-Internal-API-Key": INTERNAL_KEY})
        assert del_resp.status_code == 204

        list_resp = client.get("/internal/policies", headers={"X-Internal-API-Key": INTERNAL_KEY})
        assert list_resp.json()["total"] == 0

    def test_duplicate_policy_name_returns_409(self, client: TestClient) -> None:
        body = {"name": "unique_pol", "policy_type": "system", "scope_type": "global", "rules": {}}
        client.post("/internal/policies", json=body, headers={"X-Internal-API-Key": INTERNAL_KEY})
        resp = client.post("/internal/policies", json=body, headers={"X-Internal-API-Key": INTERNAL_KEY})
        assert resp.status_code == 409

    def test_list_policies_filter_by_scope_type(self, client: TestClient) -> None:
        client.post(
            "/internal/policies",
            json={"name": "g_pol", "policy_type": "system", "scope_type": "global", "rules": {}},
            headers={"X-Internal-API-Key": INTERNAL_KEY},
        )
        client.post(
            "/internal/policies",
            json={"name": "u_pol", "policy_type": "user", "scope_type": "user", "scope_id": 1, "rules": {}},
            headers={"X-Internal-API-Key": INTERNAL_KEY},
        )
        resp = client.get("/internal/policies?scope_type=global", headers={"X-Internal-API-Key": INTERNAL_KEY})
        assert resp.status_code == 200
        assert all(p["scope_type"] == "global" for p in resp.json()["items"])


# ---------------------------------------------------------------------------
# Workspace creation enforcement
# ---------------------------------------------------------------------------

class TestWorkspaceCreationPolicyEnforcement:
    def test_no_policy_allows_workspace_creation(self, client: TestClient) -> None:
        token = _register_and_login(client, "no_pol@test.dev")
        resp = _create_workspace(client, token)
        assert resp.status_code == 201

    def test_deny_workspace_creation_returns_403(self, client: TestClient, db_session: Session) -> None:
        token = _register_and_login(client, "deny_ws@test.dev")
        _seed_policy(db_session, rules={"allow_workspace_creation": False})
        resp = _create_workspace(client, token)
        assert resp.status_code == 403
        body = resp.json()
        assert "policy" in body

    def test_denied_image_returns_403(self, client: TestClient, db_session: Session) -> None:
        token = _register_and_login(client, "img_pol@test.dev")
        _seed_policy(db_session, rules={"allowed_runtime_images": ["ubuntu:22.04"]})
        resp = _create_workspace(client, token)
        assert resp.status_code == 403

    def test_allowed_image_returns_201(self, client: TestClient, db_session: Session) -> None:
        token = _register_and_login(client, "ok_img@test.dev")
        _seed_policy(db_session, rules={"allowed_runtime_images": ["nginx:alpine"]})
        resp = _create_workspace(client, token)
        assert resp.status_code == 201

    def test_inactive_policy_does_not_block(self, client: TestClient, db_session: Session) -> None:
        token = _register_and_login(client, "inactive@test.dev")
        now = datetime.now(timezone.utc)
        p = Policy(
            name="inactive_deny",
            policy_type=PolicyType.SYSTEM.value,
            scope_type=ScopeType.GLOBAL.value,
            rules_json={"allow_workspace_creation": False},
            is_active=False,
            created_at=now,
            updated_at=now,
        )
        db_session.add(p)
        db_session.commit()
        resp = _create_workspace(client, token)
        assert resp.status_code == 201

    def test_policy_denial_writes_audit_row(self, client: TestClient, db_session: Session) -> None:
        from app.services.audit_service.models import AuditLog
        from sqlmodel import select as sel

        token = _register_and_login(client, "audit_pol@test.dev")
        _seed_policy(db_session, rules={"allow_workspace_creation": False})
        _create_workspace(client, token)

        rows = db_session.exec(
            sel(AuditLog).where(AuditLog.action == "policy.denied")
        ).all()
        assert len(rows) >= 1
