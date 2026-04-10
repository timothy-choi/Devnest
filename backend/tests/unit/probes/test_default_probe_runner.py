"""Unit tests: ``DefaultProbeRunner`` (read-only probes; mocked runtime/topology)."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

from app.libs.probes import DefaultProbeRunner, ProbeIssueCode
from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.runtime.models import ContainerInspectionResult
from app.libs.topology.interfaces import TopologyAdapter
from app.libs.topology.models.enums import TopologyAttachmentStatus, TopologyRuntimeStatus
from app.libs.topology.results import CheckAttachmentResult, CheckTopologyResult


def _issue_codes(result: object) -> list[str]:
    return [i.code for i in result.issues]


@pytest.fixture
def mock_runtime() -> MagicMock:
    return MagicMock(spec=RuntimeAdapter)


@pytest.fixture
def mock_topology() -> MagicMock:
    return MagicMock(spec=TopologyAdapter)


@pytest.fixture
def runner(mock_runtime: MagicMock, mock_topology: MagicMock) -> DefaultProbeRunner:
    return DefaultProbeRunner(runtime=mock_runtime, topology=mock_topology)


class TestCheckContainerRunning:
    def test_running_container(self, runner: DefaultProbeRunner, mock_runtime: MagicMock) -> None:
        mock_runtime.inspect_container.return_value = ContainerInspectionResult(
            exists=True,
            container_id="cid-1",
            container_state="running",
            pid=1,
            ports=(),
            mounts=(),
        )
        out = runner.check_container_running(container_id="cid-1")
        assert out.healthy
        assert out.container_id == "cid-1"
        assert out.container_state == "running"
        assert out.issues == ()
        mock_runtime.inspect_container.assert_called_once_with(container_id="cid-1")

    def test_missing_container(self, runner: DefaultProbeRunner, mock_runtime: MagicMock) -> None:
        mock_runtime.inspect_container.return_value = ContainerInspectionResult(
            exists=False,
            container_id=None,
            container_state="missing",
            pid=None,
            ports=(),
            mounts=(),
        )
        out = runner.check_container_running(container_id="gone")
        assert not out.healthy
        assert _issue_codes(out) == [ProbeIssueCode.RUNTIME_CONTAINER_MISSING.value]

    def test_stopped_container(self, runner: DefaultProbeRunner, mock_runtime: MagicMock) -> None:
        mock_runtime.inspect_container.return_value = ContainerInspectionResult(
            exists=True,
            container_id="cid-x",
            container_state="exited",
            pid=None,
            ports=(),
            mounts=(),
        )
        out = runner.check_container_running(container_id="cid-x")
        assert not out.healthy
        assert _issue_codes(out) == [ProbeIssueCode.RUNTIME_NOT_RUNNING.value]


class TestCheckTopologyState:
    def test_healthy_topology_and_attachment_internal_endpoint_8080(
        self,
        runner: DefaultProbeRunner,
        mock_topology: MagicMock,
    ) -> None:
        mock_topology.check_topology.return_value = CheckTopologyResult(
            healthy=True,
            status=TopologyRuntimeStatus.READY,
        )
        mock_topology.check_attachment.return_value = CheckAttachmentResult(
            healthy=True,
            status=TopologyAttachmentStatus.ATTACHED,
            workspace_ip="10.20.30.40",
            internal_endpoint="ignored-by-probe",
        )
        out = runner.check_topology_state(
            topology_id="7",
            node_id="node-a",
            workspace_id="99",
            expected_port=8080,
        )
        assert out.healthy
        assert out.workspace_ip == "10.20.30.40"
        assert out.internal_endpoint == "10.20.30.40:8080"
        assert out.issues == ()
        mock_topology.check_topology.assert_called_once_with(topology_id=7, node_id="node-a")
        mock_topology.check_attachment.assert_called_once_with(
            topology_id=7,
            node_id="node-a",
            workspace_id=99,
        )

    def test_missing_attachment(self, runner: DefaultProbeRunner, mock_topology: MagicMock) -> None:
        mock_topology.check_topology.return_value = CheckTopologyResult(
            healthy=True,
            status=TopologyRuntimeStatus.READY,
        )
        mock_topology.check_attachment.return_value = CheckAttachmentResult(
            healthy=False,
            status=TopologyAttachmentStatus.DETACHED,
            issues=("db: topology attachment not found",),
        )
        out = runner.check_topology_state(
            topology_id="1",
            node_id="n1",
            workspace_id="2",
        )
        assert not out.healthy
        assert ProbeIssueCode.TOPOLOGY_ATTACHMENT_MISSING.value in _issue_codes(out)
        assert ProbeIssueCode.TOPOLOGY_WORKSPACE_IP_MISSING.value in _issue_codes(out)

    def test_missing_workspace_ip(self, runner: DefaultProbeRunner, mock_topology: MagicMock) -> None:
        mock_topology.check_topology.return_value = CheckTopologyResult(
            healthy=True,
            status=TopologyRuntimeStatus.READY,
        )
        mock_topology.check_attachment.return_value = CheckAttachmentResult(
            healthy=True,
            status=TopologyAttachmentStatus.ATTACHED,
            workspace_ip=None,
        )
        out = runner.check_topology_state(topology_id="1", node_id="n1", workspace_id="3")
        assert not out.healthy
        assert ProbeIssueCode.TOPOLOGY_WORKSPACE_IP_MISSING.value in _issue_codes(out)
        assert ProbeIssueCode.TOPOLOGY_INTERNAL_ENDPOINT_MISSING.value in _issue_codes(out)


class TestCheckServiceReachable:
    class _FakeSock:
        def close(self) -> None:
            pass

    def test_reachable(self, runner: DefaultProbeRunner) -> None:
        with patch(
            "app.libs.probes.probe_runner.socket.create_connection",
            return_value=self._FakeSock(),
        ) as cc:
            out = runner.check_service_reachable(
                workspace_ip="127.0.0.1",
                port=18080,
                timeout_seconds=1.0,
            )
        assert out.healthy
        assert out.workspace_ip == "127.0.0.1"
        assert out.port == 18080
        assert out.latency_ms is not None and out.latency_ms >= 0.0
        assert out.issues == ()
        cc.assert_called_once_with(("127.0.0.1", 18080), timeout=1.0)

    def test_timeout(self, runner: DefaultProbeRunner) -> None:
        with patch(
            "app.libs.probes.probe_runner.socket.create_connection",
            side_effect=socket.timeout(),
        ):
            out = runner.check_service_reachable(workspace_ip="192.0.2.1", port=8080, timeout_seconds=0.5)
        assert not out.healthy
        assert _issue_codes(out) == [ProbeIssueCode.SERVICE_TIMEOUT.value]

    def test_unreachable_connection_refused(self, runner: DefaultProbeRunner) -> None:
        with patch(
            "app.libs.probes.probe_runner.socket.create_connection",
            side_effect=ConnectionRefusedError(),
        ):
            out = runner.check_service_reachable(workspace_ip="192.0.2.2", port=8080)
        assert not out.healthy
        assert _issue_codes(out) == [ProbeIssueCode.SERVICE_UNREACHABLE.value]


class TestCheckWorkspaceHealth:
    def _healthy_container(self, mock_runtime: MagicMock) -> None:
        mock_runtime.inspect_container.return_value = ContainerInspectionResult(
            exists=True,
            container_id="c-ok",
            container_state="running",
            pid=1,
            ports=(),
            mounts=(),
        )

    def _healthy_topology_with_ip(
        self,
        mock_topology: MagicMock,
        *,
        ip: str = "10.88.1.10",
    ) -> None:
        mock_topology.check_topology.return_value = CheckTopologyResult(
            healthy=True,
            status=TopologyRuntimeStatus.READY,
        )
        mock_topology.check_attachment.return_value = CheckAttachmentResult(
            healthy=True,
            status=TopologyAttachmentStatus.ATTACHED,
            workspace_ip=ip,
            internal_endpoint=f"{ip}:8080",
        )

    def test_all_healthy(
        self,
        runner: DefaultProbeRunner,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
    ) -> None:
        self._healthy_container(mock_runtime)
        self._healthy_topology_with_ip(mock_topology)
        with patch(
            "app.libs.probes.probe_runner.socket.create_connection",
            return_value=TestCheckServiceReachable._FakeSock(),
        ):
            out = runner.check_workspace_health(
                workspace_id="10",
                topology_id="5",
                node_id="gw",
                container_id="c-ok",
            )
        assert out.healthy
        assert out.runtime_healthy and out.topology_healthy and out.service_healthy
        assert out.workspace_ip == "10.88.1.10"
        assert out.internal_endpoint == "10.88.1.10:8080"
        assert out.issues == ()

    def test_runtime_unhealthy(
        self,
        runner: DefaultProbeRunner,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
    ) -> None:
        mock_runtime.inspect_container.return_value = ContainerInspectionResult(
            exists=False,
            container_id=None,
            container_state="missing",
            pid=None,
            ports=(),
            mounts=(),
        )
        self._healthy_topology_with_ip(mock_topology)
        with patch(
            "app.libs.probes.probe_runner.socket.create_connection",
            return_value=TestCheckServiceReachable._FakeSock(),
        ):
            out = runner.check_workspace_health(
                workspace_id="10",
                topology_id="5",
                node_id="gw",
                container_id="missing",
            )
        assert not out.healthy
        assert not out.runtime_healthy
        assert out.topology_healthy
        assert out.service_healthy
        assert ProbeIssueCode.RUNTIME_CONTAINER_MISSING.value in _issue_codes(out)

    def test_topology_unhealthy(
        self,
        runner: DefaultProbeRunner,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
    ) -> None:
        self._healthy_container(mock_runtime)
        mock_topology.check_topology.return_value = CheckTopologyResult(
            healthy=False,
            status=TopologyRuntimeStatus.DEGRADED,
            issues=("linux: bridge missing",),
        )
        mock_topology.check_attachment.return_value = CheckAttachmentResult(
            healthy=False,
            status=TopologyAttachmentStatus.DETACHED,
            issues=("db: topology attachment not found",),
        )
        out = runner.check_workspace_health(
            workspace_id="10",
            topology_id="5",
            node_id="gw",
            container_id="c-ok",
        )
        assert not out.healthy
        assert out.runtime_healthy
        assert not out.topology_healthy
        assert not out.service_healthy
        assert ProbeIssueCode.TOPOLOGY_UNHEALTHY.value in _issue_codes(out)
        assert ProbeIssueCode.TOPOLOGY_WORKSPACE_IP_MISSING.value in _issue_codes(out)

    def test_service_unhealthy(
        self,
        runner: DefaultProbeRunner,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
    ) -> None:
        self._healthy_container(mock_runtime)
        self._healthy_topology_with_ip(mock_topology)
        with patch(
            "app.libs.probes.probe_runner.socket.create_connection",
            side_effect=ConnectionRefusedError(),
        ):
            out = runner.check_workspace_health(
                workspace_id="10",
                topology_id="5",
                node_id="gw",
                container_id="c-ok",
            )
        assert not out.healthy
        assert out.runtime_healthy and out.topology_healthy
        assert not out.service_healthy
        assert ProbeIssueCode.SERVICE_UNREACHABLE.value in _issue_codes(out)
