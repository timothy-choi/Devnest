"""Integration tests for the Quota enforcement system (real PostgreSQL, FastAPI client)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.services.policy_service.enums import ScopeType
from app.services.quota_service.models import Quota

INTERNAL_KEY = "integration-test-internal-key"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register_and_login(client: TestClient, email: str, password: str = "securepass1") -> str:
    client.post("/auth/register", json={"username": email.split("@")[0], "email": email, "password": password})
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.json()
    return resp.json()["access_token"]


def _create_workspace(client: TestClient, token: str, name: str = "ws") -> dict:
    return client.post(
        "/workspaces",
        json={"name": name, "description": "", "runtime": {"image": "nginx:alpine"}, "is_private": True},
        headers={"Authorization": f"Bearer {token}"},
    )


def _get_owner_id(client: TestClient, token: str) -> int:
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.json()
    return resp.json()["user_auth_id"]


def _seed_quota(session: Session, *, scope_type: ScopeType, scope_id: int | None = None, **limits) -> Quota:
    now = datetime.now(timezone.utc)
    q = Quota(scope_type=scope_type.value, scope_id=scope_id, created_at=now, updated_at=now, **limits)
    session.add(q)
    session.commit()
    return q


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------

class TestQuotaAdminApi:
    def test_create_quota_requires_internal_key(self, client: TestClient) -> None:
        resp = client.post("/internal/quotas", json={"scope_type": "global"})
        assert resp.status_code in (401, 503)

    def test_create_and_list_quota(self, client: TestClient) -> None:
        resp = client.post(
            "/internal/quotas",
            json={"scope_type": "global", "max_workspaces": 10},
            headers={"X-Internal-API-Key": INTERNAL_KEY},
        )
        assert resp.status_code == 201, resp.json()
        data = resp.json()
        assert data["max_workspaces"] == 10
        assert data["scope_type"] == "global"

        list_resp = client.get("/internal/quotas", headers={"X-Internal-API-Key": INTERNAL_KEY})
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] == 1

    def test_patch_quota_updates_limits(self, client: TestClient) -> None:
        create_resp = client.post(
            "/internal/quotas",
            json={"scope_type": "global", "max_workspaces": 5},
            headers={"X-Internal-API-Key": INTERNAL_KEY},
        )
        assert create_resp.status_code == 201
        qid = create_resp.json()["quota_id"]

        patch_resp = client.patch(
            f"/internal/quotas/{qid}",
            json={"max_workspaces": 20},
            headers={"X-Internal-API-Key": INTERNAL_KEY},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["max_workspaces"] == 20

    def test_patch_only_updates_provided_fields(self, client: TestClient) -> None:
        create_resp = client.post(
            "/internal/quotas",
            json={"scope_type": "global", "max_workspaces": 5, "max_snapshots": 10},
            headers={"X-Internal-API-Key": INTERNAL_KEY},
        )
        qid = create_resp.json()["quota_id"]
        patch_resp = client.patch(
            f"/internal/quotas/{qid}",
            json={"max_workspaces": 99},
            headers={"X-Internal-API-Key": INTERNAL_KEY},
        )
        assert patch_resp.json()["max_workspaces"] == 99
        assert patch_resp.json()["max_snapshots"] == 10

    def test_delete_quota(self, client: TestClient) -> None:
        create_resp = client.post(
            "/internal/quotas",
            json={"scope_type": "global"},
            headers={"X-Internal-API-Key": INTERNAL_KEY},
        )
        qid = create_resp.json()["quota_id"]
        del_resp = client.delete(f"/internal/quotas/{qid}", headers={"X-Internal-API-Key": INTERNAL_KEY})
        assert del_resp.status_code == 204

    def test_list_quotas_filter_by_scope_type(self, client: TestClient) -> None:
        client.post(
            "/internal/quotas",
            json={"scope_type": "global", "max_workspaces": 10},
            headers={"X-Internal-API-Key": INTERNAL_KEY},
        )
        client.post(
            "/internal/quotas",
            json={"scope_type": "user", "scope_id": 1, "max_workspaces": 5},
            headers={"X-Internal-API-Key": INTERNAL_KEY},
        )
        resp = client.get("/internal/quotas?scope_type=global", headers={"X-Internal-API-Key": INTERNAL_KEY})
        assert all(q["scope_type"] == "global" for q in resp.json()["items"])


# ---------------------------------------------------------------------------
# Workspace count enforcement
# ---------------------------------------------------------------------------

class TestWorkspaceQuotaEnforcement:
    def test_no_quota_allows_many_workspaces(self, client: TestClient) -> None:
        token = _register_and_login(client, "no_quota@test.dev")
        for i in range(3):
            resp = _create_workspace(client, token, name=f"ws-{i}")
            assert resp.status_code == 201

    def test_global_quota_blocks_at_limit(self, client: TestClient, db_session: Session) -> None:
        token = _register_and_login(client, "global_q@test.dev")
        _seed_quota(db_session, scope_type=ScopeType.GLOBAL, max_workspaces=2)

        assert _create_workspace(client, token, name="ws-1").status_code == 201
        assert _create_workspace(client, token, name="ws-2").status_code == 201
        resp = _create_workspace(client, token, name="ws-3")
        assert resp.status_code == 429
        body = resp.json()
        assert body["quota_field"] == "max_workspaces"
        assert body["current"] == 2
        assert body["limit"] == 2

    def test_user_quota_overrides_global(self, client: TestClient, db_session: Session) -> None:
        token = _register_and_login(client, "user_q@test.dev")
        uid = _get_owner_id(client, token)
        _seed_quota(db_session, scope_type=ScopeType.GLOBAL, max_workspaces=1)
        _seed_quota(db_session, scope_type=ScopeType.USER, scope_id=uid, max_workspaces=3)

        for i in range(3):
            assert _create_workspace(client, token, name=f"ws-{i}").status_code == 201
        assert _create_workspace(client, token, name="ws-overflow").status_code == 429

    def test_quota_exceeded_writes_audit_row(self, client: TestClient, db_session: Session) -> None:
        from app.services.audit_service.models import AuditLog
        from sqlmodel import select as sel

        token = _register_and_login(client, "qaudit@test.dev")
        _seed_quota(db_session, scope_type=ScopeType.GLOBAL, max_workspaces=0)
        _create_workspace(client, token)

        rows = db_session.exec(
            sel(AuditLog).where(AuditLog.action == "quota.exceeded")
        ).all()
        assert len(rows) >= 1


# ---------------------------------------------------------------------------
# Snapshot quota enforcement
# ---------------------------------------------------------------------------

class TestSnapshotQuotaEnforcement:
    def _setup_stopped_workspace(self, client: TestClient, db_session: Session) -> tuple[str, int, int]:
        """Register user, create workspace, fake-settle it to STOPPED."""
        token = _register_and_login(client, f"snq_{id(self)}@test.dev")
        resp = _create_workspace(client, token)
        assert resp.status_code == 201, resp.json()
        ws_id = resp.json()["workspace_id"]

        from app.services.workspace_service.models import Workspace
        ws = db_session.get(Workspace, ws_id)
        assert ws is not None
        ws.status = "STOPPED"
        db_session.add(ws)
        db_session.commit()

        uid = _get_owner_id(client, token)
        return token, ws_id, uid

    def test_snapshot_blocked_at_limit(self, client: TestClient, db_session: Session) -> None:
        token, ws_id, uid = self._setup_stopped_workspace(client, db_session)
        _seed_quota(db_session, scope_type=ScopeType.WORKSPACE, scope_id=ws_id, max_snapshots=0)

        resp = client.post(
            f"/workspaces/{ws_id}/snapshots",
            json={"name": "snap1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 429
        assert resp.json()["quota_field"] == "max_snapshots"

    def test_snapshot_allowed_under_limit(self, client: TestClient, db_session: Session) -> None:
        token, ws_id, uid = self._setup_stopped_workspace(client, db_session)
        _seed_quota(db_session, scope_type=ScopeType.WORKSPACE, scope_id=ws_id, max_snapshots=5)

        resp = client.post(
            f"/workspaces/{ws_id}/snapshots",
            json={"name": "snap1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (201, 202), resp.json()
