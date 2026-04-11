"""Integration tests: ``DefaultOrchestratorService.stop_workspace_runtime`` with real adapters.

Uses the same stack as ``test_orchestrator_bringup_integration.py``:
  - ``DockerRuntimeAdapter`` + local Docker
  - ``DbTopologyAdapter`` on the worker PostgreSQL database (``apply_linux_*`` off; no CAP_NET_ADMIN)
  - ``DefaultProbeRunner``

Bring-up uses a TCP patch for the service probe (internal ``workspace_ip`` is not host-routable). Stop
needs no patch.

**Platform:** Docker and PostgreSQL are required (see ``orchestrator_docker_client`` skip). These tests
are not ``topology_linux`` (no real bridge/veth on the host).

**Idempotency note:** The current ``stop_workspace_runtime`` success rule treats
``topology_detached=False`` (e.g. attachment already ``DETACHED``) as a failed roll-up, so a *second*
stop call may return ``success=False`` even when the runtime adapter idempotently reports a stopped
container. The idempotent test asserts no exception and a non-running container; it documents
``success`` for the second call without changing the orchestrator.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlmodel import Session, select

from app.libs.probes import DefaultProbeRunner
from app.libs.runtime.docker_runtime import DockerRuntimeAdapter
from app.libs.topology import DbTopologyAdapter
from app.libs.topology.models import Topology, TopologyAttachment
from app.libs.topology.models.enums import TopologyAttachmentStatus
from app.services.orchestrator_service import DefaultOrchestratorService

pytestmark = pytest.mark.integration


def _seed_topology(session: Session, *, spec: dict | None = None) -> int:
    t = Topology(name=f"orch-stop-{uuid.uuid4().hex[:8]}", version="v1", spec_json=spec or {})
    session.add(t)
    session.commit()
    session.refresh(t)
    assert t.topology_id is not None
    return t.topology_id


def _remove_container(client, container_id: str | None, *, name: str | None = None) -> None:
    if container_id:
        try:
            client.containers.get(container_id).remove(force=True)
            return
        except Exception:
            pass
    if name:
        try:
            client.containers.get(name).remove(force=True)
        except Exception:
            pass


class _FakeSock:
    def close(self) -> None:
        pass


def _assert_container_not_running(rt: DockerRuntimeAdapter, *, ref: str) -> None:
    ins = rt.inspect_container(container_id=ref)
    assert ins.exists is True
    state = (ins.container_state or "").strip().lower()
    assert state not in ("running", "restarting", "paused")


def _fetch_attachment(
    db_session: Session,
    *,
    topology_id: int,
    node_id: str,
    workspace_id: int,
) -> TopologyAttachment | None:
    stmt = select(TopologyAttachment).where(
        TopologyAttachment.topology_id == topology_id,
        TopologyAttachment.node_id == node_id,
        TopologyAttachment.workspace_id == workspace_id,
    )
    return db_session.exec(stmt).first()


def test_stop_workspace_runtime_happy_path_integration(
    db_session: Session,
    orchestrator_docker_client,
    orchestrator_integration_image: str,
    runtime_adapter_integration: DockerRuntimeAdapter,
    topology_adapter_integration: DbTopologyAdapter,
    tmp_path,
) -> None:
    tid = _seed_topology(
        db_session,
        spec={
            "cidr": "10.99.90.0/24",
            "gateway_ip": "10.99.90.1",
            "bridge_name": "br-orch-stop",
        },
    )
    node_id = "node-orch-stop"
    ws_num = 9000 + (uuid.uuid4().int % 1000)
    workspace_id = str(ws_num)
    container_name = f"devnest-ws-{workspace_id}"
    ws_int = int(workspace_id)

    probe = DefaultProbeRunner(runtime=runtime_adapter_integration, topology=topology_adapter_integration)
    ws_root = tmp_path / "orch-stop-ws"
    ws_root.mkdir(parents=True, exist_ok=True)

    svc = DefaultOrchestratorService(
        runtime_adapter_integration,
        topology_adapter_integration,
        probe,
        topology_id=tid,
        node_id=node_id,
        workspace_projects_base=str(ws_root),
        workspace_image=orchestrator_integration_image,
    )

    try:
        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
            return_value=_FakeSock(),
        ):
            up = svc.bring_up_workspace_runtime(workspace_id=workspace_id)
        assert up.success is True
        engine_cid = up.container_id
        assert engine_cid

        stop_out = svc.stop_workspace_runtime(workspace_id=workspace_id)

        assert stop_out.success is True
        assert stop_out.workspace_id == workspace_id
        assert stop_out.topology_detached is True
        assert stop_out.issues is None or stop_out.issues == []
        assert stop_out.container_id == engine_cid
        assert (stop_out.container_state or "").lower() in ("exited", "stopped")

        _assert_container_not_running(runtime_adapter_integration, ref=container_name)
        _assert_container_not_running(runtime_adapter_integration, ref=engine_cid)

        db_session.expire_all()
        att = _fetch_attachment(db_session, topology_id=tid, node_id=node_id, workspace_id=ws_int)
        assert att is not None
        assert att.status == TopologyAttachmentStatus.DETACHED
        assert att.container_id is None
    finally:
        _remove_container(orchestrator_docker_client, None, name=container_name)


def test_stop_workspace_runtime_idempotent_second_call_no_crash(
    db_session: Session,
    orchestrator_docker_client,
    orchestrator_integration_image: str,
    runtime_adapter_integration: DockerRuntimeAdapter,
    topology_adapter_integration: DbTopologyAdapter,
    tmp_path,
) -> None:
    """Second stop after a successful stop: no exception; container stays inactive.

    Current orchestrator may set ``success=False`` on the second call because detach returns
    ``detached=False`` when the row is already ``DETACHED``.
    """
    tid = _seed_topology(
        db_session,
        spec={
            "cidr": "10.99.91.0/24",
            "gateway_ip": "10.99.91.1",
            "bridge_name": "br-orch-stop-idem",
        },
    )
    node_id = "node-orch-stop-idem"
    ws_num = 9100 + (uuid.uuid4().int % 1000)
    workspace_id = str(ws_num)
    container_name = f"devnest-ws-{workspace_id}"

    probe = DefaultProbeRunner(runtime=runtime_adapter_integration, topology=topology_adapter_integration)
    ws_root = tmp_path / "orch-stop-idem"
    ws_root.mkdir(parents=True, exist_ok=True)

    svc = DefaultOrchestratorService(
        runtime_adapter_integration,
        topology_adapter_integration,
        probe,
        topology_id=tid,
        node_id=node_id,
        workspace_projects_base=str(ws_root),
        workspace_image=orchestrator_integration_image,
    )

    try:
        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
            return_value=_FakeSock(),
        ):
            assert svc.bring_up_workspace_runtime(workspace_id=workspace_id).success is True

        first = svc.stop_workspace_runtime(workspace_id=workspace_id)
        assert first.success is True

        second = svc.stop_workspace_runtime(workspace_id=workspace_id)
        # See module docstring: detach returns detached=False when already DETACHED → success=False.
        assert second.success is False
        assert second.container_id
        _assert_container_not_running(runtime_adapter_integration, ref=container_name)
    finally:
        _remove_container(orchestrator_docker_client, None, name=container_name)


def test_stop_workspace_runtime_topology_attachment_detached_in_db(
    db_session: Session,
    orchestrator_docker_client,
    orchestrator_integration_image: str,
    runtime_adapter_integration: DockerRuntimeAdapter,
    topology_adapter_integration: DbTopologyAdapter,
    tmp_path,
) -> None:
    """Explicit DB check: after stop, attachment is DETACHED and not in an attached-like state."""
    tid = _seed_topology(
        db_session,
        spec={
            "cidr": "10.99.92.0/24",
            "gateway_ip": "10.99.92.1",
            "bridge_name": "br-orch-stop-db",
        },
    )
    node_id = "node-orch-stop-db"
    ws_num = 9200 + (uuid.uuid4().int % 1000)
    workspace_id = str(ws_num)
    container_name = f"devnest-ws-{workspace_id}"
    ws_int = int(workspace_id)

    probe = DefaultProbeRunner(runtime=runtime_adapter_integration, topology=topology_adapter_integration)
    ws_root = tmp_path / "orch-stop-db"
    ws_root.mkdir(parents=True, exist_ok=True)

    svc = DefaultOrchestratorService(
        runtime_adapter_integration,
        topology_adapter_integration,
        probe,
        topology_id=tid,
        node_id=node_id,
        workspace_projects_base=str(ws_root),
        workspace_image=orchestrator_integration_image,
    )

    try:
        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
            return_value=_FakeSock(),
        ):
            svc.bring_up_workspace_runtime(workspace_id=workspace_id)

        before = _fetch_attachment(db_session, topology_id=tid, node_id=node_id, workspace_id=ws_int)
        assert before is not None
        assert before.status == TopologyAttachmentStatus.ATTACHED

        svc.stop_workspace_runtime(workspace_id=workspace_id)

        db_session.expire_all()
        after = _fetch_attachment(db_session, topology_id=tid, node_id=node_id, workspace_id=ws_int)
        assert after is not None
        assert after.status == TopologyAttachmentStatus.DETACHED
        assert after.container_id is None

        blocking = db_session.exec(
            select(TopologyAttachment).where(
                TopologyAttachment.topology_id == tid,
                TopologyAttachment.node_id == node_id,
                TopologyAttachment.workspace_id == ws_int,
                TopologyAttachment.status != TopologyAttachmentStatus.DETACHED,
            ),
        ).first()
        assert blocking is None
    finally:
        _remove_container(orchestrator_docker_client, None, name=container_name)
