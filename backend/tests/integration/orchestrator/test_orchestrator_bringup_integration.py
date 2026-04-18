"""Integration tests: ``DefaultOrchestratorService.bring_up_workspace_runtime`` with real adapters.

Uses:
  - ``DockerRuntimeAdapter`` against the local Docker engine
  - ``DbTopologyAdapter`` with PostgreSQL (integration ``db_session``), no host ``ip`` / veth
  - ``DefaultProbeRunner`` composed from those adapters

Internal topology ``workspace_ip`` is not host-routable in this setup. The happy-path test follows
the same pattern as ``tests/integration/probes/test_probe_runner_integration.py``: patch
``socket.create_connection`` so the service leg of the probe can succeed while exercising real
runtime inspection and topology DB state.

Requires: PostgreSQL (worker DB from ``tests/integration/conftest.py``) and a reachable Docker
daemon. If Docker is unavailable, tests are skipped.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlmodel import Session

from app.libs.common.config import get_settings
from app.libs.probes import DefaultProbeRunner
from app.libs.runtime.docker_runtime import DockerRuntimeAdapter
from app.libs.topology import DbTopologyAdapter
from app.libs.topology.models import Topology
from app.services.orchestrator_service import DefaultOrchestratorService

pytestmark = pytest.mark.integration


def _seed_topology(session: Session, *, spec: dict | None = None) -> int:
    t = Topology(name=f"orch-int-{uuid.uuid4().hex[:8]}", version="v1", spec_json=spec or {})
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


def test_bring_up_workspace_runtime_happy_path_integration(
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
            "cidr": "10.99.80.0/24",
            "gateway_ip": "10.99.80.1",
            "bridge_name": "br-orch-int",
        },
    )
    node_id = "node-orch-int"
    ws_num = 8800 + (uuid.uuid4().int % 1000)
    workspace_id = str(ws_num)
    container_name = f"devnest-ws-{workspace_id}"

    probe = DefaultProbeRunner(runtime=runtime_adapter_integration, topology=topology_adapter_integration)
    ws_root = tmp_path / "orch-ws"
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
            out = svc.bring_up_workspace_runtime(workspace_id=workspace_id)

        assert out is not None
        assert out.success is True
        assert out.probe_healthy is True
        assert out.container_id
        assert out.container_state
        assert out.netns_ref
        assert out.workspace_ip
        assert out.internal_endpoint
        assert out.node_id == node_id
        assert out.topology_id == str(tid)
        assert out.workspace_id == workspace_id
        assert out.issues is None or out.issues == []
    finally:
        _remove_container(
            orchestrator_docker_client,
            out.container_id if out else None,
            name=container_name,
        )


def test_bring_up_workspace_runtime_probe_unhealthy_when_service_unreachable(
    db_session: Session,
    orchestrator_docker_client,
    orchestrator_integration_image: str,
    runtime_adapter_integration: DockerRuntimeAdapter,
    topology_adapter_integration: DbTopologyAdapter,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVNEST_WORKSPACE_BRINGUP_IDE_TCP_WAIT_SECONDS", "3")
    monkeypatch.setenv("DEVNEST_WORKSPACE_BRINGUP_IDE_TCP_POLL_INTERVAL_SECONDS", "0.2")
    get_settings.cache_clear()

    tid = _seed_topology(
        db_session,
        spec={
            "cidr": "10.99.81.0/24",
            "gateway_ip": "10.99.81.1",
            "bridge_name": "br-orch-uhp",
        },
    )
    node_id = "node-orch-uhp"
    ws_num = 8900 + (uuid.uuid4().int % 1000)
    workspace_id = str(ws_num)
    container_name = f"devnest-ws-{workspace_id}"

    probe = DefaultProbeRunner(runtime=runtime_adapter_integration, topology=topology_adapter_integration)
    ws_root = tmp_path / "orch-ws-uhp"
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
        # No TCP patch: probe attempts real connect to internal workspace CIDR (typically fails).
        out = svc.bring_up_workspace_runtime(workspace_id=workspace_id)

        assert out.success is False
        assert out.probe_healthy is False
        assert out.container_id
        assert out.issues
        assert any("service:" in msg for msg in out.issues)
    finally:
        get_settings.cache_clear()
        _remove_container(
            orchestrator_docker_client,
            out.container_id if out else None,
            name=container_name,
        )
