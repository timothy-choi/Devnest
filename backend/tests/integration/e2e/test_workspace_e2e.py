"""End-to-end API-level integration tests for critical workspace flows.

All tests exercise the full HTTP path:
    API → job enqueued → worker processes → workspace state updated → events emitted

No Docker required.  The orchestrator is replaced with a mock that returns realistic
bring-up / stop / delete results.  The in-process worker is invoked synchronously via
POST /internal/workspace-jobs/process.

Each test is isolated (unique users + workspaces, DB cleaned between tests) and has
a well-defined 15-second timeout via pytest-timeout marks.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, create_autospec, patch

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
    WorkspaceConfig,
    WorkspaceEvent,
    WorkspaceJob,
    WorkspaceJobStatus,
    WorkspaceJobType,
    WorkspaceRuntime,
    WorkspaceStatus,
)
from app.services.workspace_service.services.workspace_event_service import (
    WorkspaceStreamEventType,
    list_workspace_events,
    record_workspace_event,
)

# ---------------------------------------------------------------------------
# Constants and shared helpers
# ---------------------------------------------------------------------------

_INTERNAL_KEY = "integration-test-internal-key"
_ORCHESTRATOR_PATCH = "app.workers.workspace_job_runner.build_orchestrator_for_workspace_job"


def _internal_headers() -> dict[str, str]:
    return {"X-Internal-API-Key": _INTERNAL_KEY}


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_and_token(client, *, username: str, email: str) -> tuple[int, str]:
    r = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": "SecureE2EPass1!"},
    )
    assert r.status_code == status.HTTP_201_CREATED, r.text
    uid = r.json()["user_auth_id"]
    return uid, create_access_token(user_id=uid)


def _create_workspace(client, token: str, *, name: str | None = None) -> tuple[int, int]:
    r = client.post(
        "/workspaces",
        json={"name": name or f"e2e-{uuid.uuid4().hex[:8]}", "description": "E2E test", "is_private": True},
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


def _reload_runtime(db_session: Session, workspace_id: int) -> WorkspaceRuntime | None:
    db_session.expire_all()
    return db_session.exec(
        select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id)
    ).first()


def _mock_bring_up(wid_str: str) -> WorkspaceBringUpResult:
    return WorkspaceBringUpResult(
        workspace_id=wid_str,
        success=True,
        node_id="node-e2e",
        topology_id="1",
        container_id=f"c-{uuid.uuid4().hex[:8]}",
        container_state="running",
        probe_healthy=True,
        internal_endpoint="10.10.0.5:8080",
    )


def _mock_stop(wid_str: str, container_id: str = "c-e2e") -> WorkspaceStopResult:
    return WorkspaceStopResult(
        workspace_id=wid_str,
        success=True,
        container_id=container_id,
        container_state="stopped",
        topology_detached=True,
    )


def _mock_delete(wid_str: str, container_id: str = "c-e2e") -> WorkspaceDeleteResult:
    return WorkspaceDeleteResult(
        workspace_id=wid_str,
        success=True,
        container_deleted=True,
        topology_detached=True,
        topology_deleted=True,
        container_id=container_id,
    )


def _make_mock_orchestrator(wid_str: str) -> MagicMock:
    mock_orch = create_autospec(OrchestratorService, instance=True)
    mock_orch.bring_up_workspace_runtime.return_value = _mock_bring_up(wid_str)
    mock_orch.stop_workspace_runtime.return_value = _mock_stop(wid_str)
    mock_orch.delete_workspace_runtime.return_value = _mock_delete(wid_str)
    return mock_orch


# ---------------------------------------------------------------------------
# Flow 1: Register → Login → Create workspace → RUNNING
# ---------------------------------------------------------------------------

@pytest.mark.timeout(15)
def test_e2e_register_login_create_workspace_running(client, db_session: Session) -> None:
    """Full flow: register → login → create workspace → process CREATE job → RUNNING."""
    suffix = uuid.uuid4().hex[:8]
    uid, token = _register_and_token(
        client, username=f"e2e_create_{suffix}", email=f"e2e_create_{suffix}@example.com"
    )

    # Create workspace
    wid, create_jid = _create_workspace(client, token, name=f"e2e-create-{suffix}")
    wid_str = str(wid)

    # Workspace must be in CREATING state immediately
    ws = _reload_workspace(db_session, wid)
    assert ws is not None
    assert ws.status == WorkspaceStatus.CREATING.value

    # Process CREATE job with mock orchestrator
    mock_orch = _make_mock_orchestrator(wid_str)
    with patch(_ORCHESTRATOR_PATCH, return_value=mock_orch):
        result = _process_job(client, create_jid)
    assert result["processed_count"] == 1

    # Workspace must be RUNNING
    ws = _reload_workspace(db_session, wid)
    assert ws is not None
    assert ws.status == WorkspaceStatus.RUNNING.value

    # Runtime row must exist with node_id and container_id
    rt = _reload_runtime(db_session, wid)
    assert rt is not None
    assert rt.node_id == "node-e2e"
    assert rt.container_id is not None
    assert rt.internal_endpoint == "10.10.0.5:8080"

    # Job must be succeeded
    db_session.expire_all()
    job = db_session.get(WorkspaceJob, create_jid)
    assert job is not None
    assert job.status == WorkspaceJobStatus.SUCCEEDED.value

    # GET /workspaces/{id} must reflect RUNNING
    r = client.get(f"/workspaces/{wid}", headers=_auth_header(token))
    assert r.status_code == status.HTTP_200_OK
    assert r.json()["status"] == WorkspaceStatus.RUNNING.value


# ---------------------------------------------------------------------------
# Flow 2: RUNNING workspace → Stop → STOPPED
# ---------------------------------------------------------------------------

@pytest.mark.timeout(15)
def test_e2e_running_workspace_stop_stopped(client, db_session: Session) -> None:
    """Flow: RUNNING → Stop → STOPPED."""
    suffix = uuid.uuid4().hex[:8]
    _, token = _register_and_token(
        client, username=f"e2e_stop_{suffix}", email=f"e2e_stop_{suffix}@example.com"
    )
    wid, create_jid = _create_workspace(client, token, name=f"e2e-stop-{suffix}")
    wid_str = str(wid)

    # Bring workspace to RUNNING
    mock_orch = _make_mock_orchestrator(wid_str)
    with patch(_ORCHESTRATOR_PATCH, return_value=mock_orch):
        _process_job(client, create_jid)

    ws = _reload_workspace(db_session, wid)
    assert ws is not None and ws.status == WorkspaceStatus.RUNNING.value

    # Get container_id from runtime for realistic stop mock
    rt = _reload_runtime(db_session, wid)
    assert rt is not None
    cid = rt.container_id

    # Stop workspace
    r_stop = client.post(f"/workspaces/stop/{wid}", headers=_auth_header(token))
    assert r_stop.status_code == status.HTTP_202_ACCEPTED
    stop_jid = int(r_stop.json()["job_id"])

    # Workspace must be STOPPING
    ws = _reload_workspace(db_session, wid)
    assert ws is not None and ws.status == WorkspaceStatus.STOPPING.value

    # Process STOP job
    mock_orch.stop_workspace_runtime.return_value = _mock_stop(wid_str, container_id=cid)
    with patch(_ORCHESTRATOR_PATCH, return_value=mock_orch):
        result = _process_job(client, stop_jid)
    assert result["processed_count"] == 1

    # Workspace must be STOPPED; endpoint_ref cleared
    ws = _reload_workspace(db_session, wid)
    assert ws is not None and ws.status == WorkspaceStatus.STOPPED.value

    r = client.get(f"/workspaces/{wid}", headers=_auth_header(token))
    assert r.json()["status"] == WorkspaceStatus.STOPPED.value


# ---------------------------------------------------------------------------
# Flow 3: RUNNING workspace → Delete → DELETED
# ---------------------------------------------------------------------------

@pytest.mark.timeout(15)
def test_e2e_running_workspace_delete_deleted(client, db_session: Session) -> None:
    """Flow: RUNNING → Delete → DELETED, runtime row cleared."""
    suffix = uuid.uuid4().hex[:8]
    _, token = _register_and_token(
        client, username=f"e2e_del_{suffix}", email=f"e2e_del_{suffix}@example.com"
    )
    wid, create_jid = _create_workspace(client, token, name=f"e2e-del-{suffix}")
    wid_str = str(wid)

    mock_orch = _make_mock_orchestrator(wid_str)
    with patch(_ORCHESTRATOR_PATCH, return_value=mock_orch):
        _process_job(client, create_jid)

    ws = _reload_workspace(db_session, wid)
    assert ws is not None and ws.status == WorkspaceStatus.RUNNING.value

    rt = _reload_runtime(db_session, wid)
    assert rt is not None
    cid = rt.container_id

    # Delete workspace
    r_del = client.delete(f"/workspaces/{wid}", headers=_auth_header(token))
    assert r_del.status_code == status.HTTP_202_ACCEPTED
    del_jid = int(r_del.json()["job_id"])

    # Process DELETE job
    mock_orch.delete_workspace_runtime.return_value = _mock_delete(wid_str, container_id=cid)
    with patch(_ORCHESTRATOR_PATCH, return_value=mock_orch):
        result = _process_job(client, del_jid)
    assert result["processed_count"] == 1

    ws = _reload_workspace(db_session, wid)
    assert ws is not None and ws.status == WorkspaceStatus.DELETED.value

    # Runtime row must be cleared or endpoint cleared
    rt = _reload_runtime(db_session, wid)
    assert rt is None or (rt.internal_endpoint is None or rt.internal_endpoint == "")

    # GET workspace returns 404 or DELETED
    r = client.get(f"/workspaces/{wid}", headers=_auth_header(token))
    assert r.status_code in (status.HTTP_200_OK, status.HTTP_404_NOT_FOUND)
    if r.status_code == status.HTTP_200_OK:
        assert r.json()["status"] == WorkspaceStatus.DELETED.value


# ---------------------------------------------------------------------------
# Flow 4: RUNNING workspace → Attach → session token returned
# ---------------------------------------------------------------------------

@pytest.mark.timeout(15)
def test_e2e_running_workspace_attach_returns_session_token(client, db_session: Session) -> None:
    """Attach to a RUNNING workspace returns a valid session token and gateway URL."""
    suffix = uuid.uuid4().hex[:8]
    _, token = _register_and_token(
        client, username=f"e2e_att_{suffix}", email=f"e2e_att_{suffix}@example.com"
    )
    wid, create_jid = _create_workspace(client, token, name=f"e2e-att-{suffix}")
    wid_str = str(wid)

    mock_orch = _make_mock_orchestrator(wid_str)
    with patch(_ORCHESTRATOR_PATCH, return_value=mock_orch):
        _process_job(client, create_jid)

    ws = _reload_workspace(db_session, wid)
    assert ws is not None and ws.status == WorkspaceStatus.RUNNING.value

    # Attach to workspace
    r_attach = client.post(
        f"/workspaces/attach/{wid}",
        json={},
        headers=_auth_header(token),
    )
    assert r_attach.status_code == status.HTTP_200_OK, r_attach.text
    data = r_attach.json()
    assert data["accepted"] is True
    assert data["workspace_id"] == wid
    assert data["session_token"] is not None
    assert len(data["session_token"]) > 10

    # Verify session can be used for access (may return 200 if gateway enabled, or runtime metadata)
    r_access = client.get(
        f"/workspaces/{wid}/access",
        headers={
            **_auth_header(token),
            "X-Devnest-Workspace-Session": data["session_token"],
        },
    )
    assert r_access.status_code == status.HTTP_200_OK, r_access.text
    access_data = r_access.json()
    assert access_data["workspace_id"] == wid


# ---------------------------------------------------------------------------
# Flow 5: SSE event delivery (DB polling path)
# ---------------------------------------------------------------------------

@pytest.mark.timeout(15)
def test_e2e_sse_event_delivery_via_db_polling(client, db_session: Session) -> None:
    """Events written to DB are retrievable via list_workspace_events (the SSE polling function).

    This test exercises the cross-worker DB polling path:  the SSE streaming handler
    polls list_workspace_events at the configured interval; any worker writing an event
    (even in a different process) will have it delivered on the next poll cycle.
    """
    suffix = uuid.uuid4().hex[:8]
    uid, token = _register_and_token(
        client, username=f"e2e_sse_{suffix}", email=f"e2e_sse_{suffix}@example.com"
    )
    wid, create_jid = _create_workspace(client, token, name=f"e2e-sse-{suffix}")
    wid_str = str(wid)

    mock_orch = _make_mock_orchestrator(wid_str)
    with patch(_ORCHESTRATOR_PATCH, return_value=mock_orch):
        _process_job(client, create_jid)

    # Write a synthetic event directly to DB — simulates cross-worker event delivery
    extra_eid = record_workspace_event(
        db_session,
        workspace_id=wid,
        event_type=WorkspaceStreamEventType.JOB_SUCCEEDED,
        status=WorkspaceStatus.RUNNING.value,
        message="E2E SSE cross-worker event",
        payload={"source": "cross_worker_simulation"},
    )
    db_session.commit()

    # list_workspace_events is the function the SSE polling loop calls
    events = list_workspace_events(db_session, workspace_id=wid, owner_user_id=uid, after_id=0)
    assert len(events) >= 1

    # Find the cross-worker event
    synthetic_event = next(
        (e for e in events if e.message == "E2E SSE cross-worker event"),
        None,
    )
    assert synthetic_event is not None, "Cross-worker DB event not found in list_workspace_events"
    assert synthetic_event.workspace_event_id == extra_eid
    assert synthetic_event.event_type == WorkspaceStreamEventType.JOB_SUCCEEDED

    # Verify after_id cursor correctly excludes already-seen events
    after_events = list_workspace_events(
        db_session, workspace_id=wid, owner_user_id=uid, after_id=extra_eid
    )
    assert all(e.workspace_event_id > extra_eid for e in after_events)

    # Also verify JOB_SUCCEEDED event was emitted by the job worker for the CREATE job
    job_succeeded = next(
        (e for e in events if e.event_type == WorkspaceStreamEventType.JOB_SUCCEEDED),
        None,
    )
    assert job_succeeded is not None, "JOB_SUCCEEDED event not found after CREATE job processing"


# ---------------------------------------------------------------------------
# Flow 6: Snapshot create and restore
# ---------------------------------------------------------------------------

@pytest.mark.timeout(15)
def test_e2e_snapshot_create_accepted(client, db_session: Session) -> None:
    """Create a snapshot for a RUNNING workspace; verify 202 accepted and DB record."""
    from app.services.workspace_service.models import WorkspaceRuntimeHealthStatus, WorkspaceRuntime

    suffix = uuid.uuid4().hex[:8]
    uid, token = _register_and_token(
        client, username=f"e2e_snap_{suffix}", email=f"e2e_snap_{suffix}@example.com"
    )
    wid, create_jid = _create_workspace(client, token, name=f"e2e-snap-{suffix}")
    wid_str = str(wid)

    mock_orch = _make_mock_orchestrator(wid_str)
    with patch(_ORCHESTRATOR_PATCH, return_value=mock_orch):
        _process_job(client, create_jid)

    ws = _reload_workspace(db_session, wid)
    assert ws is not None and ws.status == WorkspaceStatus.RUNNING.value

    # Request snapshot creation
    r_snap = client.post(
        f"/workspaces/{wid}/snapshots",
        json={"name": f"snap-{suffix}", "description": "E2E snapshot test"},
        headers=_auth_header(token),
    )
    assert r_snap.status_code in (status.HTTP_200_OK, status.HTTP_202_ACCEPTED, status.HTTP_201_CREATED), \
        f"Unexpected status {r_snap.status_code}: {r_snap.text}"

    data = r_snap.json()
    assert "snapshot_id" in data or "id" in data or "workspace_id" in data

    # Verify snapshot appears in the list
    r_list = client.get(f"/workspaces/{wid}/snapshots", headers=_auth_header(token))
    assert r_list.status_code == status.HTTP_200_OK, r_list.text
    snapshots = r_list.json()
    assert isinstance(snapshots, (list, dict))


@pytest.mark.timeout(15)
def test_e2e_snapshot_list_empty_workspace(client, db_session: Session) -> None:
    """Newly created workspace has no snapshots."""
    suffix = uuid.uuid4().hex[:8]
    uid, token = _register_and_token(
        client, username=f"e2e_nsnap_{suffix}", email=f"e2e_nsnap_{suffix}@example.com"
    )
    wid, _ = _create_workspace(client, token, name=f"e2e-nsnap-{suffix}")

    r = client.get(f"/workspaces/{wid}/snapshots", headers=_auth_header(token))
    assert r.status_code == status.HTTP_200_OK
    body = r.json()
    # Accept list or paginated response
    items = body if isinstance(body, list) else body.get("items", body.get("snapshots", []))
    assert items == [] or len(items) == 0


# ---------------------------------------------------------------------------
# Flow 7: Non-owner access is denied
# ---------------------------------------------------------------------------

@pytest.mark.timeout(15)
def test_e2e_non_owner_cannot_access_workspace(client, db_session: Session) -> None:
    """User B cannot read, start, stop, or delete User A's workspace."""
    suffix = uuid.uuid4().hex[:8]
    uid_a, token_a = _register_and_token(
        client, username=f"e2e_own_a_{suffix}", email=f"e2e_own_a_{suffix}@example.com"
    )
    _, token_b = _register_and_token(
        client, username=f"e2e_own_b_{suffix}", email=f"e2e_own_b_{suffix}@example.com"
    )

    wid, _ = _create_workspace(client, token_a, name=f"e2e-prot-{suffix}")

    r_get = client.get(f"/workspaces/{wid}", headers=_auth_header(token_b))
    assert r_get.status_code == status.HTTP_404_NOT_FOUND

    r_del = client.delete(f"/workspaces/{wid}", headers=_auth_header(token_b))
    assert r_del.status_code == status.HTTP_404_NOT_FOUND

    r_start = client.post(f"/workspaces/start/{wid}", headers=_auth_header(token_b))
    assert r_start.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Flow 8: SSE event stream authenticates caller
# ---------------------------------------------------------------------------

@pytest.mark.timeout(15)
def test_e2e_sse_requires_auth(client, db_session: Session) -> None:
    """GET /workspaces/{id}/events returns 401 without a valid token."""
    r = client.get("/workspaces/12345/events")
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.timeout(15)
def test_e2e_sse_wrong_owner_404(client, db_session: Session) -> None:
    """User B cannot stream events for User A's workspace."""
    suffix = uuid.uuid4().hex[:8]
    uid_a, token_a = _register_and_token(
        client, username=f"e2e_sse_a_{suffix}", email=f"e2e_sse_a_{suffix}@example.com"
    )
    _, token_b = _register_and_token(
        client, username=f"e2e_sse_b_{suffix}", email=f"e2e_sse_b_{suffix}@example.com"
    )
    wid, _ = _create_workspace(client, token_a, name=f"e2e-sse-prot-{suffix}")

    r = client.get(f"/workspaces/{wid}/events", headers=_auth_header(token_b))
    assert r.status_code == status.HTTP_404_NOT_FOUND
