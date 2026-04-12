"""Integration tests: audit and usage records are created by key workspace API flows.

Uses the live Postgres test DB (via `client` + `db_session` fixtures from conftest.py).
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone

import pytest
from fastapi import status
from sqlmodel import select

from app.services.audit_service.enums import AuditAction
from app.services.audit_service.models import AuditLog
from app.services.auth_service.services.auth_token import create_access_token
from app.services.usage_service.enums import UsageEventType
from app.services.usage_service.models import WorkspaceUsageRecord
from app.services.workspace_service.models import Workspace, WorkspaceConfig

pytestmark = pytest.mark.integration


def _register_and_token(client, *, username: str, email: str) -> tuple[int, str]:
    r = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": "securepass123"},
    )
    assert r.status_code == status.HTTP_201_CREATED, r.text
    uid = r.json()["user_auth_id"]
    return uid, create_access_token(user_id=uid)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_stopped_workspace(db_session, owner_id: int) -> int:
    now = datetime.now(timezone.utc)
    ws = Workspace(
        name="aud-ws",
        description="",
        owner_user_id=owner_id,
        status="STOPPED",
        is_private=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(ws)
    db_session.flush()
    db_session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}))
    db_session.commit()
    db_session.refresh(ws)
    assert ws.workspace_id is not None
    return ws.workspace_id


class TestWorkspaceCreateAudit:
    def test_create_workspace_produces_audit_row(self, client, db_session) -> None:
        uid, token = _register_and_token(client, username="aud_create", email="aud_create@test.dev")

        r = client.post(
            "/workspaces",
            json={"name": "audit-test-ws", "runtime": {"image": "nginx:alpine"}, "is_private": True},
            headers=_auth(token),
        )
        assert r.status_code == status.HTTP_202_ACCEPTED, r.text
        wid = r.json()["workspace_id"]

        rows = db_session.exec(
            select(AuditLog)
            .where(AuditLog.workspace_id == wid)
            .where(AuditLog.action == AuditAction.WORKSPACE_CREATE_REQUESTED.value),
        ).all()
        assert len(rows) == 1
        assert rows[0].actor_user_id == uid
        assert rows[0].outcome == "success"

    def test_create_workspace_produces_usage_row(self, client, db_session) -> None:
        uid, token = _register_and_token(client, username="usg_create", email="usg_create@test.dev")

        r = client.post(
            "/workspaces",
            json={"name": "usage-test-ws", "runtime": {"image": "nginx:alpine"}, "is_private": True},
            headers=_auth(token),
        )
        assert r.status_code == status.HTTP_202_ACCEPTED, r.text
        wid = r.json()["workspace_id"]

        rows = db_session.exec(
            select(WorkspaceUsageRecord)
            .where(WorkspaceUsageRecord.workspace_id == wid)
            .where(WorkspaceUsageRecord.event_type == UsageEventType.WORKSPACE_CREATED.value),
        ).all()
        assert len(rows) == 1
        assert rows[0].owner_user_id == uid


class TestWorkspaceIntentAudit:
    def test_start_intent_produces_audit_row(self, client, db_session) -> None:
        uid, token = _register_and_token(client, username="aud_start", email="aud_start@test.dev")
        wid = _seed_stopped_workspace(db_session, uid)

        r = client.post(f"/workspaces/start/{wid}", headers=_auth(token))
        assert r.status_code == status.HTTP_202_ACCEPTED, r.text

        rows = db_session.exec(
            select(AuditLog)
            .where(AuditLog.workspace_id == wid)
            .where(AuditLog.action == AuditAction.WORKSPACE_START_REQUESTED.value),
        ).all()
        assert len(rows) == 1
        assert rows[0].actor_user_id == uid

    def test_stop_intent_produces_audit_row(self, client, db_session) -> None:
        uid, token = _register_and_token(client, username="aud_stop", email="aud_stop@test.dev")
        now = datetime.now(timezone.utc)
        ws = Workspace(
            name="aud-stop-ws",
            description="",
            owner_user_id=uid,
            status="RUNNING",
            is_private=True,
            created_at=now,
            updated_at=now,
        )
        db_session.add(ws)
        db_session.flush()
        db_session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}))
        db_session.commit()
        db_session.refresh(ws)
        wid = ws.workspace_id

        r = client.post(f"/workspaces/stop/{wid}", headers=_auth(token))
        assert r.status_code == status.HTTP_202_ACCEPTED, r.text

        rows = db_session.exec(
            select(AuditLog)
            .where(AuditLog.workspace_id == wid)
            .where(AuditLog.action == AuditAction.WORKSPACE_STOP_REQUESTED.value),
        ).all()
        assert len(rows) == 1


class TestSnapshotAudit:
    def test_snapshot_create_produces_audit_row(self, client, db_session) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            import os
            os.environ["DEVNEST_SNAPSHOT_STORAGE_ROOT"] = tmp
            from app.libs.common.config import get_settings
            get_settings.cache_clear()

            uid, token = _register_and_token(client, username="aud_snap", email="aud_snap@test.dev")
            now = datetime.now(timezone.utc)
            ws = Workspace(
                name="aud-snap-ws",
                description="",
                owner_user_id=uid,
                status="RUNNING",
                is_private=True,
                created_at=now,
                updated_at=now,
            )
            db_session.add(ws)
            db_session.flush()
            db_session.add(WorkspaceConfig(workspace_id=ws.workspace_id, version=1, config_json={}))
            db_session.commit()
            db_session.refresh(ws)
            wid = ws.workspace_id

            r = client.post(
                f"/workspaces/{wid}/snapshots",
                json={"name": "audit-snap"},
                headers=_auth(token),
            )
            assert r.status_code == status.HTTP_202_ACCEPTED, r.text

            rows = db_session.exec(
                select(AuditLog)
                .where(AuditLog.workspace_id == wid)
                .where(AuditLog.action == AuditAction.WORKSPACE_SNAPSHOT_CREATE_REQUESTED.value),
            ).all()
            assert len(rows) == 1
            assert rows[0].actor_user_id == uid
            assert rows[0].resource_type == "workspace_snapshot"


class TestAdminAuditApi:
    def test_admin_can_list_workspace_audit_logs(self, client, db_session) -> None:
        uid, token = _register_and_token(client, username="aud_admin", email="aud_admin@test.dev")
        r = client.post(
            "/workspaces",
            json={"name": "admin-api-ws", "runtime": {"image": "nginx:alpine"}, "is_private": True},
            headers=_auth(token),
        )
        wid = r.json()["workspace_id"]

        resp = client.get(
            f"/internal/audit-logs/workspaces/{wid}",
            headers={"X-Internal-API-Key": "integration-test-key"},
        )
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert "items" in body
        assert isinstance(body["items"], list)
        assert len(body["items"]) >= 1

    def test_admin_can_list_user_audit_logs(self, client, db_session) -> None:
        uid, token = _register_and_token(client, username="aud_admin2", email="aud_admin2@test.dev")
        client.post(
            "/workspaces",
            json={"name": "admin-api-ws2", "runtime": {"image": "nginx:alpine"}, "is_private": True},
            headers=_auth(token),
        )

        resp = client.get(
            f"/internal/audit-logs/users/{uid}",
            headers={"X-Internal-API-Key": "integration-test-key"},
        )
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert len(body["items"]) >= 1

    def test_admin_api_requires_internal_key(self, client) -> None:
        resp = client.get("/internal/audit-logs/workspaces/1")
        assert resp.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_503_SERVICE_UNAVAILABLE)


class TestAdminUsageApi:
    def test_admin_can_get_workspace_usage(self, client, db_session) -> None:
        uid, token = _register_and_token(client, username="usg_admin", email="usg_admin@test.dev")
        r = client.post(
            "/workspaces",
            json={"name": "usg-api-ws", "runtime": {"image": "nginx:alpine"}, "is_private": True},
            headers=_auth(token),
        )
        wid = r.json()["workspace_id"]

        resp = client.get(
            f"/internal/usage/workspaces/{wid}",
            headers={"X-Internal-API-Key": "integration-test-key"},
        )
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["workspace_id"] == wid
        assert "totals" in body
        assert UsageEventType.WORKSPACE_CREATED.value in body["totals"]

    def test_admin_can_get_user_usage(self, client, db_session) -> None:
        uid, token = _register_and_token(client, username="usg_admin2", email="usg_admin2@test.dev")
        client.post(
            "/workspaces",
            json={"name": "usg-api-ws2", "runtime": {"image": "nginx:alpine"}, "is_private": True},
            headers=_auth(token),
        )

        resp = client.get(
            f"/internal/usage/users/{uid}",
            headers={"X-Internal-API-Key": "integration-test-key"},
        )
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["owner_user_id"] == uid
        assert UsageEventType.WORKSPACE_CREATED.value in body["totals_by_event"]
