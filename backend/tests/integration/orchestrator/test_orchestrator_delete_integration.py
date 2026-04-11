"""Integration tests: ``DefaultOrchestratorService.delete_workspace_runtime`` with real adapters.

Stack matches ``test_orchestrator_bringup_integration.py`` / ``test_orchestrator_stop_integration.py``:
  - ``DockerRuntimeAdapter`` + Docker
  - ``DbTopologyAdapter`` on worker PostgreSQL (``apply_linux_bridge=False``, ``apply_linux_attachment=False``)
  - ``DefaultProbeRunner`` (only needed for ``bring_up_workspace_runtime`` TCP patch)

Bring-up uses ``socket.create_connection`` patch; delete needs no patch.

**Platform:** PostgreSQL + Docker (``orchestrator_docker_client`` skips if Docker unreachable). Not
``topology_linux`` (no host bridge/veth).

**Idempotency:** A second ``delete_workspace_runtime`` after a full delete typically yields
``success=False`` because ``detach_workspace`` reports ``detached=False`` when the row is already
``DETACHED``, matching the same aggregate rule as stop/delete unit tests.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlmodel import Session, select

from app.libs.probes import DefaultProbeRunner
from app.libs.runtime.docker_runtime import DockerRuntimeAdapter
from app.libs.topology import DbTopologyAdapter
from app.libs.topology.models import Topology, TopologyAttachment, TopologyRuntime
from app.libs.topology.models.enums import TopologyAttachmentStatus
from app.services.orchestrator_service import DefaultOrchestratorService

pytestmark = pytest.mark.integration


def _seed_topology(session: Session, *, spec: dict | None = None) -> int:
    t = Topology(name=f"orch-del-{uuid.uuid4().hex[:8]}", version="v1", spec_json=spec or {})
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


def _fetch_runtime(db_session: Session, *, topology_id: int, node_id: str) -> TopologyRuntime | None:
    stmt = select(TopologyRuntime).where(
        TopologyRuntime.topology_id == topology_id,
        TopologyRuntime.node_id == node_id,
    )
    return db_session.exec(stmt).first()


def _assert_inspect_missing_or_deleted(rt: DockerRuntimeAdapter, *, ref: str) -> None:
    ins = rt.inspect_container(container_id=ref)
    assert ins.exists is False or (ins.container_state or "").strip().lower() in ("missing", "dead")


def test_delete_workspace_runtime_happy_path_single_workspace_topology_removed(
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
            "cidr": "10.99.100.0/24",
            "gateway_ip": "10.99.100.1",
            "bridge_name": "br-orch-del",
        },
    )
    node_id = "node-orch-del"
    ws_num = 10000 + (uuid.uuid4().int % 1000)
    workspace_id = str(ws_num)
    container_name = f"devnest-ws-{workspace_id}"

    probe = DefaultProbeRunner(runtime=runtime_adapter_integration, topology=topology_adapter_integration)
    ws_root = tmp_path / "orch-del-ws"
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
            "app.libs.probes.probe_runner.socket.create_connection",
            return_value=_FakeSock(),
        ):
            up = svc.bring_up_workspace_runtime(workspace_id=workspace_id)
        assert up.success is True
        engine_cid = up.container_id
        assert engine_cid

        del_out = svc.delete_workspace_runtime(workspace_id=workspace_id)

        assert del_out.success is True
        assert del_out.workspace_id == workspace_id
        assert del_out.container_deleted is True
        assert del_out.topology_detached is True
        assert del_out.topology_deleted is True
        assert del_out.issues is None or del_out.issues == []

        _assert_inspect_missing_or_deleted(runtime_adapter_integration, ref=container_name)
        _assert_inspect_missing_or_deleted(runtime_adapter_integration, ref=engine_cid)

        db_session.expire_all()
        att = _fetch_attachment(db_session, topology_id=tid, node_id=node_id, workspace_id=int(workspace_id))
        if att is not None:
            assert att.status == TopologyAttachmentStatus.DETACHED
        rt = _fetch_runtime(db_session, topology_id=tid, node_id=node_id)
        assert rt is None
    finally:
        _remove_container(orchestrator_docker_client, None, name=container_name)


def test_delete_workspace_runtime_second_call_idempotent_semantics(
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
            "cidr": "10.99.101.0/24",
            "gateway_ip": "10.99.101.1",
            "bridge_name": "br-orch-del-idem",
        },
    )
    node_id = "node-orch-del-idem"
    ws_num = 10100 + (uuid.uuid4().int % 1000)
    workspace_id = str(ws_num)
    container_name = f"devnest-ws-{workspace_id}"

    probe = DefaultProbeRunner(runtime=runtime_adapter_integration, topology=topology_adapter_integration)
    ws_root = tmp_path / "orch-del-idem"
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
            "app.libs.probes.probe_runner.socket.create_connection",
            return_value=_FakeSock(),
        ):
            assert svc.bring_up_workspace_runtime(workspace_id=workspace_id).success is True

        first = svc.delete_workspace_runtime(workspace_id=workspace_id)
        assert first.success is True

        second = svc.delete_workspace_runtime(workspace_id=workspace_id)
        assert second.success is False
        assert second.container_deleted is True
        assert second.topology_detached is False
        # First delete removed ``TopologyRuntime``; second ``delete_topology`` is the idempotent no-op path.
        assert second.topology_deleted is True

        _assert_inspect_missing_or_deleted(runtime_adapter_integration, ref=container_name)
    finally:
        _remove_container(orchestrator_docker_client, None, name=container_name)


def test_delete_workspace_runtime_topology_retained_when_second_workspace_attached(
    db_session: Session,
    orchestrator_docker_client,
    orchestrator_integration_image: str,
    runtime_adapter_integration: DockerRuntimeAdapter,
    topology_adapter_integration: DbTopologyAdapter,
    tmp_path,
) -> None:
    """Deleting one workspace must not remove node topology while another remains ATTACHED."""
    tid = _seed_topology(
        db_session,
        spec={
            "cidr": "10.99.102.0/24",
            "gateway_ip": "10.99.102.1",
            "bridge_name": "br-orch-del-shared",
        },
    )
    node_id = "node-orch-del-shared"
    ws_a = str(10200 + (uuid.uuid4().int % 500))
    ws_b = str(10700 + (uuid.uuid4().int % 500))
    name_a = f"devnest-ws-{ws_a}"
    name_b = f"devnest-ws-{ws_b}"

    probe = DefaultProbeRunner(runtime=runtime_adapter_integration, topology=topology_adapter_integration)
    base = tmp_path / "orch-del-shared"
    base.mkdir(parents=True, exist_ok=True)

    svc_a = DefaultOrchestratorService(
        runtime_adapter_integration,
        topology_adapter_integration,
        probe,
        topology_id=tid,
        node_id=node_id,
        workspace_projects_base=str(base / "a"),
        workspace_image=orchestrator_integration_image,
    )
    svc_b = DefaultOrchestratorService(
        runtime_adapter_integration,
        topology_adapter_integration,
        probe,
        topology_id=tid,
        node_id=node_id,
        workspace_projects_base=str(base / "b"),
        workspace_image=orchestrator_integration_image,
    )

    try:
        with patch(
            "app.libs.probes.probe_runner.socket.create_connection",
            return_value=_FakeSock(),
        ):
            assert svc_a.bring_up_workspace_runtime(workspace_id=ws_a).success is True
            assert svc_b.bring_up_workspace_runtime(workspace_id=ws_b).success is True

        del_out = svc_a.delete_workspace_runtime(workspace_id=ws_a)

        assert del_out.success is True
        assert del_out.container_deleted is True
        assert del_out.topology_detached is True
        assert del_out.topology_deleted is False
        assert del_out.issues and any("topology:delete_failed" in msg for msg in del_out.issues)

        db_session.expire_all()
        assert _fetch_runtime(db_session, topology_id=tid, node_id=node_id) is not None
        att_b = _fetch_attachment(db_session, topology_id=tid, node_id=node_id, workspace_id=int(ws_b))
        assert att_b is not None
        assert att_b.status == TopologyAttachmentStatus.ATTACHED

        _assert_inspect_missing_or_deleted(runtime_adapter_integration, ref=name_a)
        ins_b = runtime_adapter_integration.inspect_container(container_id=name_b)
        assert ins_b.exists is True
        assert (ins_b.container_state or "").strip().lower() == "running"

        svc_b.delete_workspace_runtime(workspace_id=ws_b)
    finally:
        _remove_container(orchestrator_docker_client, None, name=name_a)
        _remove_container(orchestrator_docker_client, None, name=name_b)
