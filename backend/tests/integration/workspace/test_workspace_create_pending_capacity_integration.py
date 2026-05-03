"""Integration: create when cluster is full → PENDING + deferred job; autoscaler demand; later RUNNING."""

from __future__ import annotations

import uuid
from unittest.mock import create_autospec, patch

import pytest
from fastapi import status
from sqlmodel import Session, select

from app.services.autoscaler_service.service import evaluate_fleet_autoscaler_tick
from app.services.auth_service.services.auth_token import create_access_token
from app.services.orchestrator_service.interfaces import OrchestratorService
from app.services.orchestrator_service.results import WorkspaceBringUpResult
from app.services.placement_service.models import ExecutionNode
from app.services.workspace_service.models import Workspace, WorkspaceJob, WorkspaceStatus
from app.services.workspace_service.models.enums import WorkspaceJobStatus, WorkspaceJobType

_INTERNAL_KEY = "integration-test-internal-key"


def _internal_headers() -> dict[str, str]:
    return {"X-Internal-API-Key": _INTERNAL_KEY}


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_and_token(client, *, username: str, email: str) -> tuple[int, str]:
    r = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": "SecurePass123!"},
    )
    assert r.status_code == status.HTTP_201_CREATED, r.text
    uid = int(r.json()["user_auth_id"])
    return uid, create_access_token(user_id=uid)


def _process_job(client, job_id: int) -> dict:
    r = client.post(
        "/internal/workspace-jobs/process",
        params={"job_id": job_id},
        headers=_internal_headers(),
    )
    assert r.status_code == status.HTTP_200_OK, r.text
    return r.json()


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


def _make_mock_orchestrator(wid_str: str) -> OrchestratorService:
    mock_orch = create_autospec(OrchestratorService, instance=True)
    mock_orch.bring_up_workspace_runtime.return_value = _mock_bring_up_result(wid_str)
    return mock_orch


@pytest.mark.integration
class TestWorkspaceCreatePendingCapacity:
    def test_create_returns_202_pending_when_cluster_full(
        self,
        client,
        db_session: Session,
    ) -> None:
        suffix = uuid.uuid4().hex[:8]
        _, token = _register_and_token(
            client,
            username=f"cap_pen_{suffix}",
            email=f"cap_pen_{suffix}@example.com",
        )

        node = db_session.exec(select(ExecutionNode)).first()
        assert node is not None
        node.max_workspaces = 1
        db_session.add(node)
        db_session.commit()

        r_a = client.post(
            "/workspaces",
            json={"name": f"holder-{suffix}", "description": "holds slot", "is_private": True},
            headers=_auth_header(token),
        )
        assert r_a.status_code == status.HTTP_202_ACCEPTED, r_a.text
        assert r_a.json()["status"] == WorkspaceStatus.PENDING.value

        r = client.post(
            "/workspaces",
            json={"name": f"pending-ws-{suffix}", "description": "capacity test", "is_private": True},
            headers=_auth_header(token),
        )
        assert r.status_code == status.HTTP_202_ACCEPTED, r.text
        data = r.json()
        assert data["status"] == WorkspaceStatus.PENDING.value
        assert "asynchronously" in (data.get("message") or "").lower()
        wid = int(data["workspace_id"])
        jid = int(data["job_id"])

        db_session.expire_all()
        ws = db_session.get(Workspace, wid)
        job = db_session.get(WorkspaceJob, jid)
        assert ws is not None and job is not None
        assert ws.status != WorkspaceStatus.ERROR.value
        assert ws.status == WorkspaceStatus.PENDING.value
        assert ws.execution_node_id is None
        assert ws.last_error_message is None
        assert job.status == WorkspaceJobStatus.QUEUED.value
        assert job.next_attempt_after is None

    def test_pending_create_job_triggers_autoscaler_scale_out_recommendation(
        self,
        client,
        db_session: Session,
    ) -> None:
        suffix = uuid.uuid4().hex[:8]
        _, token = _register_and_token(
            client,
            username=f"cap_auto_{suffix}",
            email=f"cap_auto_{suffix}@example.com",
        )

        node = db_session.exec(select(ExecutionNode)).first()
        assert node is not None
        node.max_workspaces = 1
        db_session.add(node)
        db_session.commit()

        r_a = client.post(
            "/workspaces",
            json={"name": f"holder-a-{suffix}", "description": "holds slot", "is_private": True},
            headers=_auth_header(token),
        )
        assert r_a.status_code == status.HTTP_202_ACCEPTED, r_a.text
        assert r_a.json()["status"] == WorkspaceStatus.PENDING.value

        r = client.post(
            "/workspaces",
            json={"name": f"demand-ws-{suffix}", "description": "autoscaler demand", "is_private": True},
            headers=_auth_header(token),
        )
        assert r.status_code == status.HTTP_202_ACCEPTED, r.text
        jid = int(r.json()["job_id"])

        db_session.expire_all()
        job = db_session.get(WorkspaceJob, jid)
        assert job is not None and job.status == WorkspaceJobStatus.QUEUED.value

        decision = evaluate_fleet_autoscaler_tick(db_session)
        assert decision.scale_out_recommended is True
        assert decision.capacity.pending_placement_jobs >= 1

    def test_capacity_freed_create_reaches_running(
        self,
        client,
        db_session: Session,
    ) -> None:
        suffix = uuid.uuid4().hex[:8]
        _, token = _register_and_token(
            client,
            username=f"cap_late_{suffix}",
            email=f"cap_late_{suffix}@example.com",
        )

        node = db_session.exec(select(ExecutionNode)).first()
        assert node is not None
        node.max_workspaces = 1
        db_session.add(node)
        db_session.commit()

        r1 = client.post(
            "/workspaces",
            json={"name": f"first-{suffix}", "description": "first", "is_private": True},
            headers=_auth_header(token),
        )
        assert r1.status_code == status.HTTP_202_ACCEPTED, r1.text
        assert r1.json()["status"] == WorkspaceStatus.PENDING.value
        wid1 = int(r1.json()["workspace_id"])
        jid1 = int(r1.json()["job_id"])

        mock_orch = _make_mock_orchestrator(str(wid1))
        with patch(
            "app.workers.workspace_job_runner.build_orchestrator_for_workspace_job",
            return_value=mock_orch,
        ):
            _process_job(client, jid1)

        db_session.expire_all()
        assert db_session.get(Workspace, wid1).status == WorkspaceStatus.RUNNING.value

        r2 = client.post(
            "/workspaces",
            json={"name": f"second-{suffix}", "description": "second", "is_private": True},
            headers=_auth_header(token),
        )
        assert r2.status_code == status.HTTP_202_ACCEPTED, r2.text
        assert r2.json()["status"] == WorkspaceStatus.PENDING.value
        wid2 = int(r2.json()["workspace_id"])
        jid2 = int(r2.json()["job_id"])

        node = db_session.exec(select(ExecutionNode)).first()
        node.max_workspaces = 2
        db_session.add(node)
        db_session.commit()

        db_session.expire_all()
        job2 = db_session.get(WorkspaceJob, jid2)
        assert job2 is not None
        job2.next_attempt_after = None
        db_session.add(job2)
        db_session.commit()

        mock_orch2 = _make_mock_orchestrator(str(wid2))
        with patch(
            "app.workers.workspace_job_runner.build_orchestrator_for_workspace_job",
            return_value=mock_orch2,
        ):
            _process_job(client, jid2)

        db_session.expire_all()
        ws2 = db_session.get(Workspace, wid2)
        j2 = db_session.get(WorkspaceJob, jid2)
        assert ws2 is not None and j2 is not None
        assert ws2.status == WorkspaceStatus.RUNNING.value
        assert j2.status == WorkspaceJobStatus.SUCCEEDED.value
