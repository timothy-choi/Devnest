"""Integration tests: ``DefaultOrchestratorService.restart_workspace_runtime`` with real adapters.

Stack matches ``test_orchestrator_bringup_integration.py`` / ``test_orchestrator_stop_integration.py``:
  - ``DockerRuntimeAdapter`` + Docker
  - ``DbTopologyAdapter`` on worker PostgreSQL (``apply_linux_bridge=False``, ``apply_linux_attachment=False``)
  - ``DefaultProbeRunner``

``bring_up_workspace_runtime`` (including the bring-up phase inside restart) needs the
``socket.create_connection`` patch when the probe should succeed: internal ``workspace_ip`` is not
host-routable. Wrap the full ``restart_workspace_runtime`` call in that patch for the happy path.

**Platform:** PostgreSQL + Docker (``orchestrator_docker_client`` skips if Docker unreachable). Not
``topology_linux`` (no host bridge/veth).

**Stop / bring-up edge cases:** A never-provisioned workspace has no topology attachment; detach is
an idempotent no-op (``detached=False`` without ``topology:detach_failed``). The stop phase still
succeeds when the engine has nothing to stop, and restart continues to bring-up. Bring-up failure
after a successful stop is covered by restarting *without* the TCP patch after an initial patched
bring-up (same idea as ``test_bring_up_workspace_runtime_probe_unhealthy_when_service_unreachable``).
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlmodel import Session, select

from app.libs.probes import DefaultProbeRunner
from app.libs.runtime.docker_runtime import DockerRuntimeAdapter
from app.libs.runtime.models import WORKSPACE_IDE_CONTAINER_PORT
from app.libs.topology import DbTopologyAdapter
from app.libs.topology.models import Topology, TopologyAttachment
from app.libs.topology.models.enums import TopologyAttachmentStatus
from app.services.orchestrator_service import DefaultOrchestratorService

pytestmark = pytest.mark.integration


def _seed_topology(session: Session, *, spec: dict | None = None) -> int:
    t = Topology(name=f"orch-rst-{uuid.uuid4().hex[:8]}", version="v1", spec_json=spec or {})
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


def test_restart_workspace_runtime_happy_path_integration(
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
            "cidr": "10.99.93.0/24",
            "gateway_ip": "10.99.93.1",
            "bridge_name": "br-orch-rst",
        },
    )
    node_id = "node-orch-rst"
    ws_num = 9300 + (uuid.uuid4().int % 1000)
    workspace_id = str(ws_num)
    container_name = f"devnest-ws-{workspace_id}"
    ws_int = int(workspace_id)

    probe = DefaultProbeRunner(runtime=runtime_adapter_integration, topology=topology_adapter_integration)
    ws_root = tmp_path / "orch-rst-ws"
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

    out = None
    try:
        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
            return_value=_FakeSock(),
        ):
            up0 = svc.bring_up_workspace_runtime(workspace_id=workspace_id)
        assert up0.success is True
        assert up0.workspace_ip
        assert up0.internal_endpoint

        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
            return_value=_FakeSock(),
        ):
            out = svc.restart_workspace_runtime(
                workspace_id=workspace_id,
                requested_by="integration-test",
                requested_config_version=1,
            )

        assert out is not None
        assert out.success is True
        assert out.workspace_id == workspace_id
        assert out.stop_success is True
        assert out.bringup_success is True
        assert out.container_id
        assert (out.container_state or "").strip().lower() == "running"
        assert out.workspace_ip
        assert out.internal_endpoint
        assert out.probe_healthy is True
        assert out.issues is None or out.issues == []
        assert out.node_id == node_id
        assert out.topology_id == str(tid)

        expected_ep = f"{out.workspace_ip}:{WORKSPACE_IDE_CONTAINER_PORT}"
        assert out.internal_endpoint == expected_ep

        ins = runtime_adapter_integration.inspect_container(container_id=out.container_id)
        assert ins.exists is True
        assert (ins.container_state or "").strip().lower() == "running"

        db_session.expire_all()
        att = _fetch_attachment(db_session, topology_id=tid, node_id=node_id, workspace_id=ws_int)
        assert att is not None
        assert att.status == TopologyAttachmentStatus.ATTACHED
        assert att.container_id == out.container_id
    finally:
        _remove_container(
            orchestrator_docker_client,
            out.container_id if out else None,
            name=container_name,
        )


def test_restart_workspace_runtime_never_provisioned_stop_noop_then_bringup_succeeds(
    db_session: Session,
    orchestrator_docker_client,
    orchestrator_integration_image: str,
    runtime_adapter_integration: DockerRuntimeAdapter,
    topology_adapter_integration: DbTopologyAdapter,
    tmp_path,
) -> None:
    """Topology + DB ready only: no prior bring-up. Stop is idempotent; restart runs bring-up."""
    tid = _seed_topology(
        db_session,
        spec={
            "cidr": "10.99.94.0/24",
            "gateway_ip": "10.99.94.1",
            "bridge_name": "br-orch-rst-nopro",
        },
    )
    node_id = "node-orch-rst-nopro"
    ws_num = 5_000_000 + (uuid.uuid4().int % 500_000)
    workspace_id = str(ws_num)
    container_name = f"devnest-ws-{workspace_id}"
    ws_int = int(workspace_id)

    probe = DefaultProbeRunner(runtime=runtime_adapter_integration, topology=topology_adapter_integration)
    ws_root = tmp_path / "orch-rst-nopro"
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

    out = None
    try:
        _remove_container(orchestrator_docker_client, None, name=container_name)
        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
            return_value=_FakeSock(),
        ):
            out = svc.restart_workspace_runtime(
                workspace_id=workspace_id,
                requested_by="integration-test",
                requested_config_version=1,
            )

        assert out is not None
        assert out.success is True
        assert out.stop_success is True
        assert out.bringup_success is True
        assert out.container_id
        assert (out.container_state or "").strip().lower() == "running"
        assert out.workspace_ip
        assert out.internal_endpoint
        assert out.probe_healthy is True
        assert out.issues is None or out.issues == []

        expected_ep = f"{out.workspace_ip}:{WORKSPACE_IDE_CONTAINER_PORT}"
        assert out.internal_endpoint == expected_ep

        ins = runtime_adapter_integration.inspect_container(container_id=out.container_id)
        assert ins.exists is True
        assert (ins.container_state or "").strip().lower() == "running"

        db_session.expire_all()
        att = _fetch_attachment(db_session, topology_id=tid, node_id=node_id, workspace_id=ws_int)
        assert att is not None
        assert att.status == TopologyAttachmentStatus.ATTACHED
        assert att.container_id == out.container_id
    finally:
        _remove_container(
            orchestrator_docker_client,
            out.container_id if out else None,
            name=container_name,
        )


def test_restart_workspace_runtime_bringup_probe_fails_after_stop_without_tcp_patch(
    db_session: Session,
    orchestrator_docker_client,
    orchestrator_integration_image: str,
    runtime_adapter_integration: DockerRuntimeAdapter,
    topology_adapter_integration: DbTopologyAdapter,
    tmp_path,
) -> None:
    """Initial bring-up with probe patch; restart without patch → stop OK, bring-up probe fails; rollback stops new container."""
    tid = _seed_topology(
        db_session,
        spec={
            "cidr": "10.99.95.0/24",
            "gateway_ip": "10.99.95.1",
            "bridge_name": "br-orch-rst-uhp",
        },
    )
    node_id = "node-orch-rst-uhp"
    ws_num = 9500 + (uuid.uuid4().int % 1000)
    workspace_id = str(ws_num)
    container_name = f"devnest-ws-{workspace_id}"

    probe = DefaultProbeRunner(runtime=runtime_adapter_integration, topology=topology_adapter_integration)
    ws_root = tmp_path / "orch-rst-uhp"
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

    out = None
    try:
        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
            return_value=_FakeSock(),
        ):
            assert svc.bring_up_workspace_runtime(workspace_id=workspace_id).success is True

        # No patch: bring-up phase inside restart cannot reach internal workspace IP from the host.
        out = svc.restart_workspace_runtime(workspace_id=workspace_id)

        assert out.success is False
        assert out.stop_success is True
        assert out.bringup_success is False
        assert out.probe_healthy is False
        assert out.issues
        assert any("service:" in msg for msg in out.issues)

        assert out.container_id
        ins = runtime_adapter_integration.inspect_container(container_id=out.container_id)
        assert ins.exists is True
        # Compensating rollback stops the failed bring-up container (no leaked running workload).
        assert (ins.container_state or "").strip().lower() in ("exited", "stopped")
    finally:
        _remove_container(
            orchestrator_docker_client,
            out.container_id if out else None,
            name=container_name,
        )
