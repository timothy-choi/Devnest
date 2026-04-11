"""Integration tests: ``DefaultOrchestratorService.check_workspace_runtime_health`` (read-only).

Uses the same stack as other orchestrator integration tests. Probe TCP leg is patched like bring-up
when the internal workspace IP is not host-routable.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlmodel import Session

from app.libs.probes import DefaultProbeRunner
from app.libs.runtime.docker_runtime import DockerRuntimeAdapter
from app.libs.topology import DbTopologyAdapter
from app.libs.topology.models import Topology
from app.services.orchestrator_service import DefaultOrchestratorService

pytestmark = pytest.mark.integration


def _seed_topology(session: Session, *, spec: dict | None = None) -> int:
    t = Topology(name=f"orch-hlth-{uuid.uuid4().hex[:8]}", version="v1", spec_json=spec or {})
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


def test_check_workspace_runtime_health_after_bring_up_integration(
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
            "cidr": "10.99.99.0/24",
            "gateway_ip": "10.99.99.1",
            "bridge_name": "br-orch-hlth",
        },
    )
    node_id = "node-orch-hlth"
    ws_num = 9900 + (uuid.uuid4().int % 1000)
    workspace_id = str(ws_num)
    container_name = f"devnest-ws-{workspace_id}"

    probe = DefaultProbeRunner(runtime=runtime_adapter_integration, topology=topology_adapter_integration)
    ws_root = tmp_path / "orch-hlth"
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

        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
            return_value=_FakeSock(),
        ):
            out = svc.check_workspace_runtime_health(workspace_id=workspace_id)

        assert out.success is True
        assert out.probe_healthy is True
        assert out.workspace_id == workspace_id
        assert out.container_id
        assert (out.container_state or "").strip().lower() == "running"
        assert out.issues is None or out.issues == []
    finally:
        _remove_container(orchestrator_docker_client, None, name=container_name)
