"""End-to-end API integration test: workspace lifecycle (register → auth → create → start → stop → delete).

Uses a real PostgreSQL database (via the shared integration conftest fixtures) and mocks the
orchestrator so no Docker daemon is required. Verifies HTTP responses, workspace state transitions,
and job creation at each lifecycle step. Includes negative cases (409 conflict, 404 not found).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, create_autospec

import pytest
from fastapi import status
from sqlmodel import Session, select

from app.services.auth_service.services.auth_token import create_access_token
from app.services.orchestrator_service.interfaces import OrchestratorService
from app.services.orchestrator_service.results import (
    WorkspaceBringUpResult,
    WorkspaceDeleteResult,
    WorkspaceStopResult,
)
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceJob,
    WorkspaceRuntime,
    WorkspaceStatus,
    WorkspaceJobStatus,
    WorkspaceJobType,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INTERNAL_KEY = "integration-test-internal-key"


def _internal_headers() -> dict[str, str]:
    return {"X-Internal-API-Key": _INTERNAL_KEY}


def _register_and_token(client, *, username: str, email: str) -> tuple[int, str]:
    r = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": "SecurePass123!"},
    )
    assert r.status_code == status.HTTP_201_CREATED, r.text
    uid = r.json()["user_auth_id"]
    return uid, create_access_token(user_id=uid)


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_workspace(client, token: str, *, name: str | None = None) -> tuple[int, int]:
    r = client.post(
        "/workspaces",
        json={
            "name": name or f"lifecycle-{uuid.uuid4().hex[:10]}",
            "description": "lifecycle integration test",
            "is_private": True,
        },
        headers=_auth_header(token),
    )
    assert r.status_code == status.HTTP_202_ACCEPTED, r.text
    data = r.json()
    return int(data["workspace_id"]), int(data["job_id"])


def _process_job(client, job_id: int) -> dict:
    r = client.post(
        "/internal/workspace-jobs/process",
        params={"job_id": job_id},
        headers=_internal_headers(),
    )
    assert r.status_code == status.HTTP_200_OK, r.text
    return r.json()


def _reload_workspace(db_session: Session, workspace_id: int) -> Workspace | None:
    db_session.expire_all()
    return db_session.get(Workspace, workspace_id)


def _reload_job(db_session: Session, job_id: int) -> WorkspaceJob | None:
    db_session.expire_all()
    return db_session.get(WorkspaceJob, job_id)


def _runtime_for(db_session: Session, workspace_id: int) -> WorkspaceRuntime | None:
    db_session.expire_all()
    return db_session.exec(
        select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id)
    ).first()


def _mock_bring_up_result(wid: str) -> WorkspaceBringUpResult:
    return WorkspaceBringUpResult(
        workspace_id=wid,
        success=True,
        node_id="node-1",
        topology_id="1",
        container_id=f"mock-container-{uuid.uuid4().hex[:8]}",
        container_state="running",
        probe_healthy=True,
        internal_endpoint="10.0.0.2:8080",
    )


def _mock_stop_result(wid: str, *, container_id: str = "mock-container") -> WorkspaceStopResult:
    return WorkspaceStopResult(
        workspace_id=wid,
        success=True,
        container_id=container_id,
        container_state="stopped",
        topology_detached=True,
    )


def _mock_delete_result(wid: str, *, container_id: str = "mock-container") -> WorkspaceDeleteResult:
    return WorkspaceDeleteResult(
        workspace_id=wid,
        success=True,
        container_deleted=True,
        topology_detached=True,
        topology_deleted=True,
        container_id=container_id,
    )


# ---------------------------------------------------------------------------
# Full lifecycle tests (mocked orchestrator)
# ---------------------------------------------------------------------------


class TestWorkspaceLifecycleApiHappyPath:
    """Full lifecycle: register → create → start → stop → delete with mocked orchestrator."""

    def _make_mock_orchestrator(self, wid_str: str) -> MagicMock:
        mock_orch = create_autospec(OrchestratorService, instance=True)
        mock_orch.bring_up_workspace_runtime.return_value = _mock_bring_up_result(wid_str)
        mock_orch.stop_workspace_runtime.return_value = _mock_stop_result(wid_str)
        mock_orch.delete_workspace_runtime.return_value = _mock_delete_result(wid_str)
        return mock_orch

    def test_register_and_authenticate(self, client) -> None:
        suffix = uuid.uuid4().hex[:8]
        r = client.post(
            "/auth/register",
            json={
                "username": f"life_reg_{suffix}",
                "email": f"life_reg_{suffix}@example.com",
                "password": "SecurePass123!",
            },
        )
        assert r.status_code == status.HTTP_201_CREATED
        data = r.json()
        assert "user_auth_id" in data

        login = client.post(
            "/auth/login",
            json={
                "username": f"life_reg_{suffix}",
                "password": "SecurePass123!",
            },
        )
        assert login.status_code == status.HTTP_200_OK
        assert "access_token" in login.json()

    def test_create_workspace_enqueues_create_job(self, client, db_session: Session) -> None:
        suffix = uuid.uuid4().hex[:8]
        _, token = _register_and_token(
            client,
            username=f"life_create_{suffix}",
            email=f"life_create_{suffix}@example.com",
        )
        r = client.post(
            "/workspaces",
            json={"name": f"ws-{suffix}", "description": "lifecycle test", "is_private": True},
            headers=_auth_header(token),
        )
        assert r.status_code == status.HTTP_202_ACCEPTED
        data = r.json()
        assert data["status"] == WorkspaceStatus.PENDING.value
        assert "workspace_id" in data
        assert "job_id" in data

        wid = int(data["workspace_id"])
        jid = int(data["job_id"])

        ws = _reload_workspace(db_session, wid)
        assert ws is not None
        assert ws.status == WorkspaceStatus.PENDING.value
        job = _reload_job(db_session, jid)
        assert job is not None
        assert job.status == WorkspaceJobStatus.QUEUED.value
        assert job.job_type == WorkspaceJobType.CREATE.value

    def test_full_lifecycle_create_start_stop_delete(self, client, db_session: Session) -> None:
        suffix = uuid.uuid4().hex[:8]
        _, token = _register_and_token(
            client,
            username=f"life_full_{suffix}",
            email=f"life_full_{suffix}@example.com",
        )

        # Create workspace
        wid, create_jid = _create_workspace(client, token, name=f"life-full-{suffix}")
        wid_str = str(wid)

        mock_orch = self._make_mock_orchestrator(wid_str)

        with patch(
            "app.workers.workspace_job_runner.build_orchestrator_for_workspace_job",
            return_value=mock_orch,
        ):
            # Process CREATE job → workspace becomes RUNNING
            tick = _process_job(client, create_jid)
            assert tick["processed_count"] == 1

        ws = _reload_workspace(db_session, wid)
        job = _reload_job(db_session, create_jid)
        rt = _runtime_for(db_session, wid)

        assert ws is not None and ws.status == WorkspaceStatus.RUNNING.value
        assert job is not None and job.status == WorkspaceJobStatus.SUCCEEDED.value
        assert rt is not None
        assert rt.container_id is not None
        assert rt.node_id == "node-1"

        # Stop workspace
        r_stop = client.post(f"/workspaces/stop/{wid}", headers=_auth_header(token))
        assert r_stop.status_code == status.HTTP_202_ACCEPTED
        stop_jid = int(r_stop.json()["job_id"])
        assert r_stop.json()["job_type"] == WorkspaceJobType.STOP.value

        # Verify STOPPING state was set
        ws = _reload_workspace(db_session, wid)
        assert ws is not None and ws.status == WorkspaceStatus.STOPPING.value

        mock_orch.stop_workspace_runtime.return_value = _mock_stop_result(
            wid_str, container_id=rt.container_id
        )
        with patch(
            "app.workers.workspace_job_runner.build_orchestrator_for_workspace_job",
            return_value=mock_orch,
        ):
            _process_job(client, stop_jid)

        ws = _reload_workspace(db_session, wid)
        assert ws is not None and ws.status == WorkspaceStatus.STOPPED.value

        # Verify container_id was passed to orchestrator stop
        mock_orch.stop_workspace_runtime.assert_called_once_with(
            workspace_id=wid_str,
            container_id=rt.container_id,
            requested_by=mock_orch.stop_workspace_runtime.call_args.kwargs.get("requested_by"),
        )

        # Delete workspace
        r_delete = client.delete(f"/workspaces/{wid}", headers=_auth_header(token))
        assert r_delete.status_code == status.HTTP_202_ACCEPTED
        delete_jid = int(r_delete.json()["job_id"])

        with patch(
            "app.workers.workspace_job_runner.build_orchestrator_for_workspace_job",
            return_value=mock_orch,
        ):
            _process_job(client, delete_jid)

        ws = _reload_workspace(db_session, wid)
        assert ws is not None and ws.status == WorkspaceStatus.DELETED.value

    def test_start_stopped_workspace(self, client, db_session: Session) -> None:
        """A STOPPED workspace can be started again (enqueues START job)."""
        suffix = uuid.uuid4().hex[:8]
        _, token = _register_and_token(
            client,
            username=f"life_start_{suffix}",
            email=f"life_start_{suffix}@example.com",
        )
        wid, create_jid = _create_workspace(client, token)
        wid_str = str(wid)
        mock_orch = self._make_mock_orchestrator(wid_str)

        with patch(
            "app.workers.workspace_job_runner.build_orchestrator_for_workspace_job",
            return_value=mock_orch,
        ):
            _process_job(client, create_jid)
            assert _reload_workspace(db_session, wid).status == WorkspaceStatus.RUNNING.value

            r_stop = client.post(f"/workspaces/stop/{wid}", headers=_auth_header(token))
            assert r_stop.status_code == status.HTTP_202_ACCEPTED
            _process_job(client, int(r_stop.json()["job_id"]))
            assert _reload_workspace(db_session, wid).status == WorkspaceStatus.STOPPED.value

            r_start = client.post(f"/workspaces/start/{wid}", headers=_auth_header(token))
            assert r_start.status_code == status.HTTP_202_ACCEPTED
            assert r_start.json()["job_type"] == WorkspaceJobType.START.value
            _process_job(client, int(r_start.json()["job_id"]))

        ws = _reload_workspace(db_session, wid)
        assert ws is not None and ws.status == WorkspaceStatus.RUNNING.value


# ---------------------------------------------------------------------------
# Negative / conflict / not-found cases
# ---------------------------------------------------------------------------


class TestWorkspaceLifecycleApiNegativeCases:
    def test_create_workspace_with_same_name_is_allowed(self, client, db_session: Session) -> None:
        """The API does not enforce unique workspace names per user; both creations succeed."""
        suffix = uuid.uuid4().hex[:8]
        _, token = _register_and_token(
            client,
            username=f"life_dup_{suffix}",
            email=f"life_dup_{suffix}@example.com",
        )
        ws_name = f"dup-ws-{suffix}"

        r1 = client.post(
            "/workspaces",
            json={"name": ws_name, "is_private": True},
            headers=_auth_header(token),
        )
        assert r1.status_code == status.HTTP_202_ACCEPTED

        r2 = client.post(
            "/workspaces",
            json={"name": ws_name, "is_private": True},
            headers=_auth_header(token),
        )
        assert r2.status_code == status.HTTP_202_ACCEPTED

    def test_stop_nonexistent_workspace_returns_404(self, client) -> None:
        suffix = uuid.uuid4().hex[:8]
        _, token = _register_and_token(
            client,
            username=f"life_404stop_{suffix}",
            email=f"life_404stop_{suffix}@example.com",
        )
        r = client.post("/workspaces/stop/999999", headers=_auth_header(token))
        assert r.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_nonexistent_workspace_returns_404(self, client) -> None:
        suffix = uuid.uuid4().hex[:8]
        _, token = _register_and_token(
            client,
            username=f"life_404del_{suffix}",
            email=f"life_404del_{suffix}@example.com",
        )
        r = client.delete("/workspaces/999999", headers=_auth_header(token))
        assert r.status_code == status.HTTP_404_NOT_FOUND

    def test_start_running_workspace_returns_409(self, client, db_session: Session) -> None:
        """Starting an already-RUNNING workspace returns 409 (WorkspaceBusyError)."""
        suffix = uuid.uuid4().hex[:8]
        _, token = _register_and_token(
            client,
            username=f"life_409start_{suffix}",
            email=f"life_409start_{suffix}@example.com",
        )
        wid, create_jid = _create_workspace(client, token)
        wid_str = str(wid)

        mock_orch = create_autospec(OrchestratorService, instance=True)
        mock_orch.bring_up_workspace_runtime.return_value = _mock_bring_up_result(wid_str)

        with patch(
            "app.workers.workspace_job_runner.build_orchestrator_for_workspace_job",
            return_value=mock_orch,
        ):
            _process_job(client, create_jid)

        assert _reload_workspace(db_session, wid).status == WorkspaceStatus.RUNNING.value

        r_start = client.post(f"/workspaces/start/{wid}", headers=_auth_header(token))
        assert r_start.status_code == status.HTTP_409_CONFLICT

    def test_stop_non_running_workspace_returns_409(self, client, db_session: Session) -> None:
        """Stopping a PENDING workspace (CREATE not yet RUNNING) returns 409."""
        suffix = uuid.uuid4().hex[:8]
        _, token = _register_and_token(
            client,
            username=f"life_409stop_{suffix}",
            email=f"life_409stop_{suffix}@example.com",
        )
        # Create workspace but don't process the CREATE job — it stays PENDING until placement/bring-up.
        wid, _ = _create_workspace(client, token)
        assert _reload_workspace(db_session, wid).status == WorkspaceStatus.PENDING.value

        r_stop = client.post(f"/workspaces/stop/{wid}", headers=_auth_header(token))
        assert r_stop.status_code == status.HTTP_409_CONFLICT

    def test_unauthenticated_request_returns_401(self, client) -> None:
        r = client.post("/workspaces", json={"name": "unauth-ws", "is_private": True})
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    def test_other_users_workspace_returns_403_or_404(self, client, db_session: Session) -> None:
        """A user cannot stop another user's workspace."""
        suffix = uuid.uuid4().hex[:8]
        _, token_owner = _register_and_token(
            client,
            username=f"life_owner_{suffix}",
            email=f"life_owner_{suffix}@example.com",
        )
        _, token_other = _register_and_token(
            client,
            username=f"life_other_{suffix}",
            email=f"life_other_{suffix}@example.com",
        )

        wid, create_jid = _create_workspace(client, token_owner)
        mock_orch = create_autospec(OrchestratorService, instance=True)
        mock_orch.bring_up_workspace_runtime.return_value = _mock_bring_up_result(str(wid))

        with patch(
            "app.workers.workspace_job_runner.build_orchestrator_for_workspace_job",
            return_value=mock_orch,
        ):
            _process_job(client, create_jid)

        r = client.post(f"/workspaces/stop/{wid}", headers=_auth_header(token_other))
        assert r.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# Job creation verification
# ---------------------------------------------------------------------------


class TestWorkspaceJobCreation:
    def test_workspace_create_creates_single_create_job(
        self, client, db_session: Session
    ) -> None:
        suffix = uuid.uuid4().hex[:8]
        _, token = _register_and_token(
            client,
            username=f"life_job_{suffix}",
            email=f"life_job_{suffix}@example.com",
        )
        r = client.post(
            "/workspaces",
            json={"name": f"job-test-{suffix}", "is_private": False},
            headers=_auth_header(token),
        )
        assert r.status_code == status.HTTP_202_ACCEPTED
        wid = int(r.json()["workspace_id"])
        jid = int(r.json()["job_id"])

        jobs = db_session.exec(
            select(WorkspaceJob).where(WorkspaceJob.workspace_id == wid)
        ).all()
        assert len(jobs) == 1
        assert jobs[0].workspace_job_id == jid
        assert jobs[0].job_type == WorkspaceJobType.CREATE.value
        assert jobs[0].status == WorkspaceJobStatus.QUEUED.value

    def test_stop_creates_stop_job_with_correct_type(
        self, client, db_session: Session
    ) -> None:
        suffix = uuid.uuid4().hex[:8]
        _, token = _register_and_token(
            client,
            username=f"life_stopjob_{suffix}",
            email=f"life_stopjob_{suffix}@example.com",
        )
        wid, create_jid = _create_workspace(client, token)
        mock_orch = create_autospec(OrchestratorService, instance=True)
        mock_orch.bring_up_workspace_runtime.return_value = _mock_bring_up_result(str(wid))

        with patch(
            "app.workers.workspace_job_runner.build_orchestrator_for_workspace_job",
            return_value=mock_orch,
        ):
            _process_job(client, create_jid)

        r_stop = client.post(f"/workspaces/stop/{wid}", headers=_auth_header(token))
        assert r_stop.status_code == status.HTTP_202_ACCEPTED
        data = r_stop.json()
        assert data["job_type"] == WorkspaceJobType.STOP.value
        stop_jid = int(data["job_id"])

        stop_job = _reload_job(db_session, stop_jid)
        assert stop_job is not None
        assert stop_job.workspace_id == wid
        assert stop_job.job_type == WorkspaceJobType.STOP.value
        assert stop_job.status == WorkspaceJobStatus.QUEUED.value
