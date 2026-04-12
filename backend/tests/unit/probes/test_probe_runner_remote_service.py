"""DefaultProbeRunner service probe when ``service_reachability_runner`` is set."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.libs.probes.constants import ProbeIssueCode
from app.libs.probes.probe_runner import DefaultProbeRunner


def test_remote_service_probe_uses_runner_ipv4() -> None:
    runtime = MagicMock()
    topology = MagicMock()
    runner = MagicMock()
    pr = DefaultProbeRunner(runtime=runtime, topology=topology, service_reachability_runner=runner)
    res = pr.check_service_reachable(workspace_ip="10.1.2.3", port=8080, timeout_seconds=2.0)
    assert res.healthy is True
    runner.run.assert_called_once_with(["timeout", "2", "nc", "-z", "10.1.2.3", "8080"])


def test_remote_service_probe_rejects_non_ipv4() -> None:
    runtime = MagicMock()
    topology = MagicMock()
    runner = MagicMock()
    pr = DefaultProbeRunner(runtime=runtime, topology=topology, service_reachability_runner=runner)
    res = pr.check_service_reachable(workspace_ip="fe80::1", port=8080, timeout_seconds=2.0)
    assert res.healthy is False
    assert res.issues
    assert res.issues[0].code == ProbeIssueCode.SERVICE_CONNECT_ERROR.value
    runner.run.assert_not_called()
