"""Integration tests: ``DefaultOrchestratorService.update_workspace_runtime`` with real adapters.

Stack matches ``test_orchestrator_restart_integration.py`` / ``test_orchestrator_bringup_integration.py``:
  - ``DockerRuntimeAdapter`` + Docker
  - ``DbTopologyAdapter`` on worker PostgreSQL (``apply_linux_*`` off)
  - ``DefaultProbeRunner``

``bring_up_workspace_runtime`` and the bring-up phase inside restart-based update need the
``socket.create_connection`` patch when the probe should succeed (internal ``workspace_ip`` is not
host-routable). Wrap those calls in the patch.

V1 config version is read from the container label ``devnest.config_version``, set on **new**
container create when bring-up passes ``requested_config_version``. ``DockerRuntimeAdapter.ensure_container``
returns early for an **existing** stopped container and does not merge new labels, so after a
restart-based update the engine label may still show the pre-stop version; integration assertions
use orchestrator results, running state, internal endpoint, and DB attachment—not post-restart label
as the source of truth for ``requested_config_version``.

**Platform:** PostgreSQL + Docker (``orchestrator_docker_client`` skip). Not ``topology_linux``.

**Failure paths:** Engine stop failure during an update is not injected here (would require brittle
Docker manipulation or mocks); that stays in ``tests/unit/orchestrator/test_orchestrator_update.py``.
Bring-up / probe failure after a successful stop is covered by bumping the config version *without*
the TCP patch on ``update_workspace_runtime`` (same pattern as restart/bring-up unhealthy tests).
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlmodel import Session, select

from app.libs.probes import DefaultProbeRunner
from app.libs.runtime.docker_runtime import DockerRuntimeAdapter
from app.libs.runtime.models import WORKSPACE_IDE_CONTAINER_PORT, ContainerInspectionResult
from app.libs.topology import DbTopologyAdapter
from app.libs.topology.models import Topology, TopologyAttachment
from app.libs.topology.models.enums import TopologyAttachmentStatus
from app.services.orchestrator_service import DefaultOrchestratorService

pytestmark = pytest.mark.integration

_CONFIG_VERSION_LABEL = "devnest.config_version"


def _seed_topology(session: Session, *, spec: dict | None = None) -> int:
    t = Topology(name=f"orch-upd-{uuid.uuid4().hex[:8]}", version="v1", spec_json=spec or {})
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


def _label_config_version(ins: ContainerInspectionResult) -> int | None:
    for k, v in ins.labels:
        if k == _CONFIG_VERSION_LABEL:
            try:
                return int(str(v).strip(), 10)
            except ValueError:
                return None
    return None


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


def test_update_workspace_runtime_noop_when_label_matches_requested_integration(
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
            "cidr": "10.99.96.0/24",
            "gateway_ip": "10.99.96.1",
            "bridge_name": "br-orch-upd-noop",
        },
    )
    node_id = "node-orch-upd-noop"
    ws_num = 9600 + (uuid.uuid4().int % 1000)
    workspace_id = str(ws_num)
    container_name = f"devnest-ws-{workspace_id}"
    ws_int = int(workspace_id)
    cfg_ver = 11

    probe = DefaultProbeRunner(runtime=runtime_adapter_integration, topology=topology_adapter_integration)
    ws_root = tmp_path / "orch-upd-noop"
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
            up0 = svc.bring_up_workspace_runtime(
                workspace_id=workspace_id,
                requested_config_version=cfg_ver,
            )
        assert up0.success is True
        cid_before = up0.container_id
        assert cid_before
        ins0 = runtime_adapter_integration.inspect_container(container_id=container_name)
        assert _label_config_version(ins0) == cfg_ver

        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
            return_value=_FakeSock(),
        ):
            out = svc.update_workspace_runtime(
                workspace_id=workspace_id,
                requested_config_version=cfg_ver,
                requested_by="noop-integration",
            )

        assert out is not None
        assert out.success is True
        assert out.workspace_id == workspace_id
        assert out.no_op is True
        assert out.update_strategy == "noop"
        assert out.current_config_version == cfg_ver
        assert out.requested_config_version == cfg_ver
        assert out.issues is None or out.issues == []
        assert out.probe_healthy is True
        assert out.container_id == cid_before
        assert (out.container_state or "").strip().lower() == "running"
        assert out.node_id == node_id
        assert out.topology_id == str(tid)

        ins_after = runtime_adapter_integration.inspect_container(container_id=container_name)
        assert ins_after.exists is True
        assert (ins_after.container_id or "") == (cid_before or "")
        assert _label_config_version(ins_after) == cfg_ver

        db_session.expire_all()
        att = _fetch_attachment(db_session, topology_id=tid, node_id=node_id, workspace_id=ws_int)
        assert att is not None
        assert att.status == TopologyAttachmentStatus.ATTACHED
    finally:
        _remove_container(
            orchestrator_docker_client,
            out.container_id if out else None,
            name=container_name,
        )


def test_update_workspace_runtime_restart_happy_path_integration(
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
            "cidr": "10.99.97.0/24",
            "gateway_ip": "10.99.97.1",
            "bridge_name": "br-orch-upd-rst",
        },
    )
    node_id = "node-orch-upd-rst"
    ws_num = 9700 + (uuid.uuid4().int % 1000)
    workspace_id = str(ws_num)
    container_name = f"devnest-ws-{workspace_id}"
    ws_int = int(workspace_id)
    v_before = 3
    v_after = 7

    probe = DefaultProbeRunner(runtime=runtime_adapter_integration, topology=topology_adapter_integration)
    ws_root = tmp_path / "orch-upd-rst"
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
            assert svc.bring_up_workspace_runtime(
                workspace_id=workspace_id,
                requested_config_version=v_before,
            ).success is True

        ins_mid = runtime_adapter_integration.inspect_container(container_id=container_name)
        assert _label_config_version(ins_mid) == v_before

        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
            return_value=_FakeSock(),
        ):
            out = svc.update_workspace_runtime(
                workspace_id=workspace_id,
                requested_config_version=v_after,
                requested_by="upd-integration",
            )

        assert out is not None
        assert out.success is True
        assert out.no_op is False
        assert out.update_strategy == "restart"
        assert out.current_config_version == v_before
        assert out.requested_config_version == v_after
        assert out.stop_success is True
        assert out.bringup_success is True
        assert out.container_id
        assert (out.container_state or "").strip().lower() == "running"
        assert out.node_id == node_id
        assert out.topology_id == str(tid)
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


def test_update_workspace_runtime_restart_probe_unhealthy_without_tcp_patch_integration(
    db_session: Session,
    orchestrator_docker_client,
    orchestrator_integration_image: str,
    runtime_adapter_integration: DockerRuntimeAdapter,
    topology_adapter_integration: DbTopologyAdapter,
    tmp_path,
) -> None:
    """Version bump without probe patch → stop succeeds, bring-up probe fails; rollback stops new container."""
    tid = _seed_topology(
        db_session,
        spec={
            "cidr": "10.99.98.0/24",
            "gateway_ip": "10.99.98.1",
            "bridge_name": "br-orch-upd-uhp",
        },
    )
    node_id = "node-orch-upd-uhp"
    ws_num = 9800 + (uuid.uuid4().int % 1000)
    workspace_id = str(ws_num)
    container_name = f"devnest-ws-{workspace_id}"

    probe = DefaultProbeRunner(runtime=runtime_adapter_integration, topology=topology_adapter_integration)
    ws_root = tmp_path / "orch-upd-uhp"
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
            assert svc.bring_up_workspace_runtime(
                workspace_id=workspace_id,
                requested_config_version=2,
            ).success is True

        out = svc.update_workspace_runtime(
            workspace_id=workspace_id,
            requested_config_version=9,
        )

        assert out.success is False
        assert out.no_op is False
        assert out.update_strategy == "restart"
        assert out.stop_success is True
        assert out.bringup_success is False
        assert out.probe_healthy is False
        assert out.issues
        assert any("service:" in msg for msg in out.issues)

        assert out.container_id
        ins = runtime_adapter_integration.inspect_container(container_id=out.container_id)
        assert ins.exists is True
        assert (ins.container_state or "").strip().lower() in ("exited", "stopped")
    finally:
        _remove_container(
            orchestrator_docker_client,
            out.container_id if out else None,
            name=container_name,
        )
