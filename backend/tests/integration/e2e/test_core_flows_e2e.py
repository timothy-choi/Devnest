"""Core user flows: merge-tier integration E2E (HTTP APIs + internal job processor + PostgreSQL).

These complement ``test_workspace_e2e.py`` with:
- ``POST /auth/login``-issued tokens on non-trivial paths (repo import, snapshots)
- repo import → ``REPO_IMPORT`` job row
- snapshot create + restore through HTTP routes and ``/internal/workspace-jobs/process``
- orchestrator stop failure → durable cleanup debt (production-confidence cleanup contract)

Heavier paths (real Docker/EC2, full Git clone) stay in ``slow``/nightly tests; see ``docs/TESTING.md``.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from fastapi import status
from sqlmodel import Session, select

from app.libs.common.config import get_settings
from app.services.cleanup_service import CLEANUP_SCOPE_STOP_INCOMPLETE
from app.services.orchestrator_service.interfaces import OrchestratorService
from app.services.orchestrator_service.results import (
    WorkspaceBringUpResult,
    WorkspaceSnapshotOperationResult,
    WorkspaceStopResult,
)
from app.services.workspace_service.models import (
    Workspace,
    WorkspaceCleanupTask,
    WorkspaceJob,
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceSnapshot,
    WorkspaceStatus,
)
from app.services.workspace_service.models.enums import WorkspaceCleanupTaskStatus, WorkspaceSnapshotStatus

pytestmark = pytest.mark.integration

_INTERNAL_KEY = "integration-test-internal-key"
_ORCHESTRATOR_PATCH = "app.workers.workspace_job_runner.build_orchestrator_for_workspace_job"


def _internal_headers() -> dict[str, str]:
    return {"X-Internal-API-Key": _INTERNAL_KEY}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_and_login(client, *, username: str, email: str, password: str = "CoreFlowE2EPass1!") -> tuple[int, str]:
    r = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    assert r.status_code == status.HTTP_201_CREATED, r.text
    uid = r.json()["user_auth_id"]
    lr = client.post("/auth/login", json={"username": username, "password": password})
    assert lr.status_code == status.HTTP_200_OK, lr.text
    return uid, lr.json()["access_token"]


def _process_job(client, db_session: Session, job_id: int) -> dict:
    r = client.post(
        "/internal/workspace-jobs/process",
        params={"job_id": job_id},
        headers=_internal_headers(),
    )
    assert r.status_code == status.HTTP_200_OK, r.text
    body = r.json()
    assert body["processed_count"] == 1
    db_session.expire_all()
    return body


def _export_writes_minimal_archive(*, workspace_id: str, archive_path: str) -> WorkspaceSnapshotOperationResult:
    p = Path(archive_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * 32)
    return WorkspaceSnapshotOperationResult(
        workspace_id=workspace_id,
        success=True,
        size_bytes=int(p.stat().st_size),
        issues=None,
    )


@pytest.mark.timeout(30)
def test_merge_tier_http_login_repo_import_enqueues_job(client, db_session: Session) -> None:
    """Register + login via HTTP; import-repo returns 202 and persists a REPO_IMPORT job."""
    suffix = uuid.uuid4().hex[:8]
    _, token = _register_and_login(
        client,
        username=f"core_repo_{suffix}",
        email=f"core_repo_{suffix}@example.com",
    )
    r_ws = client.post(
        "/workspaces",
        json={"name": f"core-import-{suffix}", "description": "core flow", "is_private": True},
        headers=_auth(token),
    )
    assert r_ws.status_code == status.HTTP_202_ACCEPTED, r_ws.text
    wid = int(r_ws.json()["workspace_id"])

    r_imp = client.post(
        f"/workspaces/{wid}/import-repo",
        json={
            "repo_url": "https://github.com/octocat/Hello-World.git",
            "branch": "master",
            "clone_dir": "/workspace/hello",
        },
        headers=_auth(token),
    )
    assert r_imp.status_code == status.HTTP_202_ACCEPTED, r_imp.text
    jid = int(r_imp.json()["job_id"])
    job = db_session.get(WorkspaceJob, jid)
    assert job is not None
    assert job.job_type == WorkspaceJobType.REPO_IMPORT.value
    assert job.status == WorkspaceJobStatus.QUEUED.value


@pytest.mark.timeout(90)
def test_merge_tier_snapshot_create_restore_http_jobs(
    client,
    db_session: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP login → RUNNING workspace → snapshot job → stop → restore job (mock orchestrator I/O)."""
    monkeypatch.setenv("DEVNEST_SNAPSHOT_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()

    suffix = uuid.uuid4().hex[:8]
    _, token = _register_and_login(
        client,
        username=f"core_snap_{suffix}",
        email=f"core_snap_{suffix}@example.com",
    )
    r_ws = client.post(
        "/workspaces",
        json={"name": f"core-snap-{suffix}", "description": "snapshot e2e", "is_private": True},
        headers=_auth(token),
    )
    assert r_ws.status_code == status.HTTP_202_ACCEPTED
    wid = int(r_ws.json()["workspace_id"])
    create_jid = int(r_ws.json()["job_id"])
    wid_str = str(wid)

    mock_orch = create_autospec(OrchestratorService, instance=True)
    mock_orch.bring_up_workspace_runtime.return_value = WorkspaceBringUpResult(
        workspace_id=wid_str,
        success=True,
        node_id="node-core-snap",
        topology_id="1",
        container_id=f"c-{suffix[:6]}",
        container_state="running",
        probe_healthy=True,
        internal_endpoint="10.10.9.2:8080",
    )
    mock_orch.stop_workspace_runtime.return_value = WorkspaceStopResult(
        workspace_id=wid_str,
        success=True,
        container_id=f"c-{suffix[:6]}",
        container_state="stopped",
        topology_detached=True,
    )
    mock_orch.export_workspace_filesystem_snapshot.side_effect = _export_writes_minimal_archive
    mock_orch.import_workspace_filesystem_snapshot.return_value = WorkspaceSnapshotOperationResult(
        workspace_id=wid_str,
        success=True,
        size_bytes=32,
        issues=None,
    )

    with patch(_ORCHESTRATOR_PATCH, return_value=mock_orch):
        _process_job(client, db_session, create_jid)
        ws = db_session.get(Workspace, wid)
        assert ws is not None and ws.status == WorkspaceStatus.RUNNING.value

        r_snap = client.post(
            f"/workspaces/{wid}/snapshots",
            json={"name": f"snap-{suffix}", "description": "core e2e"},
            headers=_auth(token),
        )
        assert r_snap.status_code == status.HTTP_202_ACCEPTED, r_snap.text
        snap_jid = int(r_snap.json()["job_id"])
        sid = int(r_snap.json()["snapshot_id"])
        _process_job(client, db_session, snap_jid)

        snap = db_session.get(WorkspaceSnapshot, sid)
        assert snap is not None
        assert snap.status == WorkspaceSnapshotStatus.AVAILABLE.value

        r_stop = client.post(f"/workspaces/stop/{wid}", headers=_auth(token))
        assert r_stop.status_code == status.HTTP_202_ACCEPTED
        stop_jid = int(r_stop.json()["job_id"])
        _process_job(client, db_session, stop_jid)
        assert db_session.get(Workspace, wid).status == WorkspaceStatus.STOPPED.value

        r_rest = client.post(f"/snapshots/{sid}/restore", headers=_auth(token))
        assert r_rest.status_code == status.HTTP_202_ACCEPTED, r_rest.text
        restore_jid = int(r_rest.json()["job_id"])
        _process_job(client, db_session, restore_jid)

    rjob = db_session.get(WorkspaceJob, restore_jid)
    assert rjob is not None
    assert rjob.job_type == WorkspaceJobType.SNAPSHOT_RESTORE.value
    assert rjob.status == WorkspaceJobStatus.SUCCEEDED.value
    snap2 = db_session.get(WorkspaceSnapshot, sid)
    assert snap2 is not None and snap2.status == WorkspaceSnapshotStatus.AVAILABLE.value


@pytest.mark.timeout(30)
def test_merge_tier_create_failure_not_running_and_job_failed(
    client,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed bring-up surfaces ERROR workspace + failed job (no false RUNNING).

    CREATE jobs retry once by default; zero backoff so both attempts are processable in-process.
    """
    monkeypatch.setenv("WORKSPACE_JOB_RETRY_BACKOFF_SECONDS", "0")
    get_settings.cache_clear()

    suffix = uuid.uuid4().hex[:8]
    _, token = _register_and_login(
        client,
        username=f"core_fail_{suffix}",
        email=f"core_fail_{suffix}@example.com",
    )
    r_ws = client.post(
        "/workspaces",
        json={"name": f"core-fail-{suffix}", "description": "x", "is_private": True},
        headers=_auth(token),
    )
    assert r_ws.status_code == status.HTTP_202_ACCEPTED
    wid = int(r_ws.json()["workspace_id"])
    jid = int(r_ws.json()["job_id"])
    wid_str = str(wid)

    mock_orch = create_autospec(OrchestratorService, instance=True)
    mock_orch.bring_up_workspace_runtime.return_value = WorkspaceBringUpResult(
        workspace_id=wid_str,
        success=False,
        issues=["container:probe_failed:simulated"],
    )
    with patch(_ORCHESTRATOR_PATCH, return_value=mock_orch):
        _process_job(client, db_session, jid)
        _process_job(client, db_session, jid)

    ws = db_session.get(Workspace, wid)
    assert ws is not None
    assert ws.status == WorkspaceStatus.ERROR.value
    job = db_session.get(WorkspaceJob, jid)
    assert job is not None and job.status == WorkspaceJobStatus.FAILED.value
    r_get = client.get(f"/workspaces/{wid}", headers=_auth(token))
    assert r_get.status_code == status.HTTP_200_OK
    assert r_get.json()["status"] == WorkspaceStatus.ERROR.value


@pytest.mark.timeout(30)
def test_merge_tier_stop_failure_creates_durable_cleanup_debt(client, db_session: Session) -> None:
    """Stop orchestrator failure records cleanup debt (stop_incomplete) and workspace ERROR."""
    suffix = uuid.uuid4().hex[:8]
    _, token = _register_and_login(
        client,
        username=f"core_stop_{suffix}",
        email=f"core_stop_{suffix}@example.com",
    )
    r_ws = client.post(
        "/workspaces",
        json={"name": f"core-stop-{suffix}", "description": "x", "is_private": True},
        headers=_auth(token),
    )
    wid = int(r_ws.json()["workspace_id"])
    create_jid = int(r_ws.json()["job_id"])
    wid_str = str(wid)

    mock_orch = MagicMock(spec=OrchestratorService)
    mock_orch.bring_up_workspace_runtime.return_value = WorkspaceBringUpResult(
        workspace_id=wid_str,
        success=True,
        node_id="node-stop-fail",
        topology_id="9",
        container_id=f"c-{suffix[:6]}",
        container_state="running",
        probe_healthy=True,
        internal_endpoint="10.10.8.1:8080",
    )
    mock_orch.stop_workspace_runtime.return_value = WorkspaceStopResult(
        workspace_id=wid_str,
        success=False,
        issues=["docker:stop_failed:simulated"],
    )

    with patch(_ORCHESTRATOR_PATCH, return_value=mock_orch):
        _process_job(client, db_session, create_jid)
        r_stop = client.post(f"/workspaces/stop/{wid}", headers=_auth(token))
        assert r_stop.status_code == status.HTTP_202_ACCEPTED
        stop_jid = int(r_stop.json()["job_id"])
        _process_job(client, db_session, stop_jid)

    ws = db_session.get(Workspace, wid)
    assert ws is not None and ws.status == WorkspaceStatus.ERROR.value
    debt = db_session.exec(
        select(WorkspaceCleanupTask).where(
            WorkspaceCleanupTask.workspace_id == wid,
            WorkspaceCleanupTask.scope == CLEANUP_SCOPE_STOP_INCOMPLETE,
            WorkspaceCleanupTask.status == WorkspaceCleanupTaskStatus.PENDING.value,
        ),
    ).first()
    assert debt is not None
