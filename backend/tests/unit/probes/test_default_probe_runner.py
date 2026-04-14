"""Unit tests: ``DefaultProbeRunner`` (read-only probes; mocked runtime/topology)."""

from __future__ import annotations

import errno
import socket
from unittest.mock import MagicMock, patch

import pytest

from app.libs.probes import DefaultProbeRunner, ProbeIssueCode
from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.topology.errors import TopologyHealthCheckError
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
    def test_empty_container_id(self, runner: DefaultProbeRunner) -> None:
        out = runner.check_container_running(container_id="  ")
        assert not out.healthy
        assert _issue_codes(out) == [ProbeIssueCode.PROBE_EXECUTION_FAILED.value]
        assert out.issues[0].component == "probe"

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
        assert out.issues[0].component == "runtime"
        assert "exist" in out.issues[0].message.lower()

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
        assert out.container_state == "exited"
        assert out.issues[0].component == "runtime"

    def test_inspect_raises_maps_to_state_unknown(
        self,
        runner: DefaultProbeRunner,
        mock_runtime: MagicMock,
    ) -> None:
        mock_runtime.inspect_container.side_effect = RuntimeError("runtime backend unavailable")
        out = runner.check_container_running(container_id="cid-err")
        assert not out.healthy
        assert out.container_id == "cid-err"
        assert out.container_state is None
        assert _issue_codes(out) == [ProbeIssueCode.RUNTIME_CONTAINER_STATE_UNKNOWN.value]
        assert out.issues[0].component == "runtime"
        assert "inspect_container failed" in out.issues[0].message

    def test_container_state_blank_is_unknown(
        self,
        runner: DefaultProbeRunner,
        mock_runtime: MagicMock,
    ) -> None:
        mock_runtime.inspect_container.return_value = ContainerInspectionResult(
            exists=True,
            container_id="cid-u",
            container_state="",
            pid=1,
            ports=(),
            mounts=(),
        )
        out = runner.check_container_running(container_id="cid-u")
        assert not out.healthy
        assert _issue_codes(out) == [ProbeIssueCode.RUNTIME_CONTAINER_STATE_UNKNOWN.value]


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

    def test_unhealthy_topology_runtime_attachment_still_healthy(
        self,
        runner: DefaultProbeRunner,
        mock_topology: MagicMock,
    ) -> None:
        mock_topology.check_topology.return_value = CheckTopologyResult(
            healthy=False,
            status=TopologyRuntimeStatus.DEGRADED,
            issues=("linux: bridge device missing",),
        )
        mock_topology.check_attachment.return_value = CheckAttachmentResult(
            healthy=True,
            status=TopologyAttachmentStatus.ATTACHED,
            workspace_ip="10.50.60.70",
        )
        out = runner.check_topology_state(
            topology_id="3",
            node_id="node-z",
            workspace_id="40",
            expected_port=9090,
        )
        assert not out.healthy
        assert out.topology_id == 3
        assert out.node_id == "node-z"
        assert out.workspace_id == 40
        assert out.workspace_ip == "10.50.60.70"
        assert out.internal_endpoint == "10.50.60.70:9090"
        assert _issue_codes(out) == [ProbeIssueCode.TOPOLOGY_UNHEALTHY.value]
        assert "bridge device missing" in out.issues[0].message

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

    def test_invalid_ids_probe_component(self, runner: DefaultProbeRunner, mock_topology: MagicMock) -> None:
        out = runner.check_topology_state(topology_id="x", node_id="n1", workspace_id="1")
        assert not out.healthy
        assert out.issues[0].code == ProbeIssueCode.PROBE_EXECUTION_FAILED.value
        assert out.issues[0].component == "probe"
        mock_topology.check_topology.assert_not_called()

    def test_check_topology_raises_uses_probe_component(
        self,
        runner: DefaultProbeRunner,
        mock_topology: MagicMock,
    ) -> None:
        mock_topology.check_topology.side_effect = TopologyHealthCheckError("unavailable")
        out = runner.check_topology_state(topology_id="1", node_id="n1", workspace_id="2")
        assert not out.healthy
        assert out.issues[0].code == ProbeIssueCode.PROBE_EXECUTION_FAILED.value
        assert out.issues[0].component == "probe"

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

    def test_invalid_expected_port_internal_endpoint_missing_with_workspace_ip(
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
            workspace_ip="10.0.0.8",
        )
        out = runner.check_topology_state(
            topology_id="1",
            node_id="n1",
            workspace_id="3",
            expected_port=0,
        )
        assert not out.healthy
        assert out.workspace_ip == "10.0.0.8"
        assert out.internal_endpoint is None
        assert _issue_codes(out) == [ProbeIssueCode.TOPOLOGY_INTERNAL_ENDPOINT_MISSING.value]
        assert "expected_port=0" in out.issues[0].message


class TestCheckServiceReachable:
    class _FakeSock:
        def close(self) -> None:
            pass

    def test_reachable(self, runner: DefaultProbeRunner) -> None:
        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
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
            "app.libs.probes.probe_runner._probe_create_connection",
            side_effect=socket.timeout(),
        ):
            out = runner.check_service_reachable(workspace_ip="192.0.2.1", port=8080, timeout_seconds=0.5)
        assert not out.healthy
        assert _issue_codes(out) == [ProbeIssueCode.SERVICE_TIMEOUT.value]

    def test_unreachable_connection_refused(self, runner: DefaultProbeRunner) -> None:
        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
            side_effect=ConnectionRefusedError(),
        ):
            out = runner.check_service_reachable(workspace_ip="192.0.2.2", port=8080)
        assert not out.healthy
        assert _issue_codes(out) == [ProbeIssueCode.SERVICE_UNREACHABLE.value]
        assert out.workspace_ip == "192.0.2.2"
        assert out.port == 8080
        assert out.latency_ms is None
        assert out.issues[0].component == "service"

    def test_unreachable_oserror_enetunreach(self, runner: DefaultProbeRunner) -> None:
        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
            side_effect=OSError(errno.ENETUNREACH, "Network is unreachable"),
        ):
            out = runner.check_service_reachable(workspace_ip="192.0.2.3", port=443)
        assert not out.healthy
        assert _issue_codes(out) == [ProbeIssueCode.SERVICE_UNREACHABLE.value]
        assert "unreachable" in out.issues[0].message.lower()

    def test_connect_error_gaierror(self, runner: DefaultProbeRunner) -> None:
        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
            side_effect=socket.gaierror(8, "nodename nor servname"),
        ):
            out = runner.check_service_reachable(workspace_ip="no-such-host.invalid", port=8080)
        assert not out.healthy
        assert _issue_codes(out) == [ProbeIssueCode.SERVICE_CONNECT_ERROR.value]
        assert "resolution" in out.issues[0].message.lower()

    def test_connect_error_generic_oserror(self, runner: DefaultProbeRunner) -> None:
        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
            side_effect=OSError(errno.EIO, "I/O error"),
        ):
            out = runner.check_service_reachable(workspace_ip="127.0.0.1", port=8080)
        assert not out.healthy
        assert _issue_codes(out) == [ProbeIssueCode.SERVICE_CONNECT_ERROR.value]

    def test_invalid_timeout_seconds(self, runner: DefaultProbeRunner) -> None:
        out = runner.check_service_reachable(workspace_ip="127.0.0.1", port=8080, timeout_seconds=float("nan"))
        assert not out.healthy
        assert _issue_codes(out) == [ProbeIssueCode.SERVICE_CONNECT_ERROR.value]


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
        import urllib.error
        self._healthy_container(mock_runtime)
        self._healthy_topology_with_ip(mock_topology)
        fake_http = MagicMock()
        fake_http.status = 200
        fake_http.__enter__ = lambda s: s
        fake_http.__exit__ = MagicMock(return_value=False)
        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
            return_value=TestCheckServiceReachable._FakeSock(),
        ), patch(
            "app.libs.probes.probe_runner._probe_urlopen",
            return_value=fake_http,
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
        fake_http = MagicMock()
        fake_http.status = 200
        fake_http.__enter__ = lambda s: s
        fake_http.__exit__ = MagicMock(return_value=False)
        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
            return_value=TestCheckServiceReachable._FakeSock(),
        ), patch(
            "app.libs.probes.probe_runner._probe_urlopen",
            return_value=fake_http,
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
        assert _issue_codes(out).count(ProbeIssueCode.TOPOLOGY_WORKSPACE_IP_MISSING.value) == 1

    def test_service_unhealthy(
        self,
        runner: DefaultProbeRunner,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
    ) -> None:
        self._healthy_container(mock_runtime)
        self._healthy_topology_with_ip(mock_topology)
        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
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
        assert out.workspace_ip == "10.88.1.10"
        assert out.internal_endpoint == "10.88.1.10:8080"

    def test_aggregate_service_timeout_preserves_workspace_ip_and_internal_endpoint(
        self,
        runner: DefaultProbeRunner,
        mock_runtime: MagicMock,
        mock_topology: MagicMock,
    ) -> None:
        self._healthy_container(mock_runtime)
        self._healthy_topology_with_ip(mock_topology, ip="10.99.1.2")
        with patch(
            "app.libs.probes.probe_runner._probe_create_connection",
            side_effect=socket.timeout(),
        ):
            out = runner.check_workspace_health(
                workspace_id="11",
                topology_id="6",
                node_id="gw2",
                container_id="c-ok",
                expected_port=8080,
                timeout_seconds=0.25,
            )
        assert not out.healthy
        assert out.runtime_healthy is True
        assert out.topology_healthy is True
        assert out.service_healthy is False
        assert out.workspace_ip == "10.99.1.2"
        assert out.internal_endpoint == "10.99.1.2:8080"
        assert _issue_codes(out).count(ProbeIssueCode.SERVICE_TIMEOUT.value) == 1
        assert out.container_state == "running"
