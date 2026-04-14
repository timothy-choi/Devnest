"""Integration tests: ``DefaultProbeRunner`` with real PostgreSQL topology state (no orchestrator).

Requires the same PostgreSQL setup as ``tests/integration`` (see root ``tests/conftest.py`` /
``DATABASE_URL``). Linux bridge/veth is skipped via env + explicit adapter flags so tests stay
unprivileged. Service reachability uses a short-lived localhost TCP listener (no routing to
workspace CIDR).
"""

from __future__ import annotations

import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session

from app.libs.probes import DefaultProbeRunner, ProbeIssueCode
from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.runtime.models import ContainerInspectionResult, WORKSPACE_IDE_CONTAINER_PORT
from app.libs.topology import DbTopologyAdapter
from app.libs.topology.models import Topology


def _seed_topology(session: Session, *, spec: dict | None = None) -> int:
    t = Topology(name="probe-int-topo", version="v1", spec_json=spec or {})
    session.add(t)
    session.commit()
    session.refresh(t)
    assert t.topology_id is not None
    return t.topology_id


@pytest.fixture
def topology_adapter(db_session: Session) -> DbTopologyAdapter:
    """Real DB adapter; skip host ``ip`` / netns (integration DB only)."""
    return DbTopologyAdapter(
        db_session,
        apply_linux_bridge=False,
        apply_linux_attachment=False,
    )


@pytest.fixture
def mock_runtime_running() -> MagicMock:
    rt = MagicMock(spec=RuntimeAdapter)
    rt.inspect_container.return_value = ContainerInspectionResult(
        exists=True,
        container_id="int-container-1",
        container_state="running",
        pid=4242,
        ports=(),
        mounts=(),
    )
    return rt


@pytest.fixture
def local_tcp_port() -> int:
    """Ephemeral 127.0.0.1 listener; accepts one connection then closes."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def _accept_one() -> None:
        try:
            conn, _ = listener.accept()
            conn.close()
        except OSError:
            pass
        finally:
            try:
                listener.close()
            except OSError:
                pass

    th = threading.Thread(target=_accept_one, daemon=True)
    th.start()
    time.sleep(0.05)
    try:
        yield port
    finally:
        th.join(timeout=5.0)


def test_check_topology_state_returns_workspace_ip_and_internal_endpoint(
    db_session: Session,
    topology_adapter: DbTopologyAdapter,
    mock_runtime_running: MagicMock,
) -> None:
    tid = _seed_topology(
        db_session,
        spec={"cidr": "10.99.10.0/24", "gateway_ip": "10.99.10.1", "bridge_name": "br-probe"},
    )
    topology_adapter.ensure_node_topology(topology_id=tid, node_id="node-probe")
    lease = topology_adapter.allocate_workspace_ip(
        topology_id=tid,
        node_id="node-probe",
        workspace_id=4242,
    )
    topology_adapter.attach_workspace(
        topology_id=tid,
        node_id="node-probe",
        workspace_id=4242,
        container_id="c-probe",
        netns_ref="/proc/4242/ns/net",
        workspace_ip=lease.workspace_ip,
    )

    runner = DefaultProbeRunner(runtime=mock_runtime_running, topology=topology_adapter)
    out = runner.check_topology_state(
        topology_id=str(tid),
        node_id="node-probe",
        workspace_id="4242",
        expected_port=WORKSPACE_IDE_CONTAINER_PORT,
    )

    assert out.healthy
    assert out.topology_id == tid
    assert out.workspace_id == 4242
    assert out.workspace_ip == lease.workspace_ip
    assert out.internal_endpoint == f"{lease.workspace_ip}:{WORKSPACE_IDE_CONTAINER_PORT}"


def test_check_workspace_health_aggregate_with_persisted_state(
    db_session: Session,
    topology_adapter: DbTopologyAdapter,
    mock_runtime_running: MagicMock,
) -> None:
    tid = _seed_topology(
        db_session,
        spec={"cidr": "10.99.11.0/24", "gateway_ip": "10.99.11.1"},
    )
    topology_adapter.ensure_node_topology(topology_id=tid, node_id="node-wh")
    lease = topology_adapter.allocate_workspace_ip(
        topology_id=tid,
        node_id="node-wh",
        workspace_id=5151,
    )
    topology_adapter.attach_workspace(
        topology_id=tid,
        node_id="node-wh",
        workspace_id=5151,
        container_id="c-wh",
        netns_ref="/proc/5151/ns/net",
        workspace_ip=lease.workspace_ip,
    )

    class _FakeSock:
        def close(self) -> None:
            pass

    fake_http = MagicMock()
    fake_http.status = 200
    fake_http.__enter__ = lambda s: s
    fake_http.__exit__ = MagicMock(return_value=False)

    runner = DefaultProbeRunner(runtime=mock_runtime_running, topology=topology_adapter)
    with patch(
        "app.libs.probes.probe_runner._probe_create_connection",
        return_value=_FakeSock(),
    ), patch(
        "app.libs.probes.probe_runner._probe_urlopen",
        return_value=fake_http,
    ):
        agg = runner.check_workspace_health(
            workspace_id="5151",
            topology_id=str(tid),
            node_id="node-wh",
            container_id="int-container-1",
            expected_port=WORKSPACE_IDE_CONTAINER_PORT,
            timeout_seconds=1.0,
        )

    assert agg.healthy
    assert agg.runtime_healthy and agg.topology_healthy and agg.service_healthy
    assert agg.workspace_id == 5151
    assert agg.workspace_ip == lease.workspace_ip
    assert agg.internal_endpoint == f"{lease.workspace_ip}:{WORKSPACE_IDE_CONTAINER_PORT}"
    assert agg.container_state == "running"
    assert agg.issues == ()


def test_check_topology_state_unhealthy_when_runtime_row_missing(
    db_session: Session,
    topology_adapter: DbTopologyAdapter,
    mock_runtime_running: MagicMock,
) -> None:
    tid = _seed_topology(
        db_session,
        spec={"cidr": "10.99.12.0/24", "gateway_ip": "10.99.12.1"},
    )
    runner = DefaultProbeRunner(runtime=mock_runtime_running, topology=topology_adapter)
    out = runner.check_topology_state(
        topology_id=str(tid),
        node_id="no-runtime-here",
        workspace_id="1",
    )
    assert not out.healthy
    assert any(i.code == ProbeIssueCode.TOPOLOGY_UNHEALTHY.value for i in out.issues)


def test_check_service_reachable_local_tcp_fixture(
    mock_runtime_running: MagicMock,
    local_tcp_port: int,
) -> None:
    """Real TCP connect to localhost; topology adapter unused (mocked)."""
    dummy_topology = MagicMock()
    runner = DefaultProbeRunner(runtime=mock_runtime_running, topology=dummy_topology)
    out = runner.check_service_reachable(
        workspace_ip="127.0.0.1",
        port=local_tcp_port,
        timeout_seconds=2.0,
    )
    assert out.healthy
    assert out.workspace_ip == "127.0.0.1"
    assert out.port == local_tcp_port
    assert out.latency_ms is not None and out.latency_ms >= 0.0
    assert out.issues == ()
    dummy_topology.assert_not_called()
