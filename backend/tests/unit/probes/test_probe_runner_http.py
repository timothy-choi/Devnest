"""Unit tests: DefaultProbeRunner.check_service_http and check_workspace_health HTTP integration."""

from __future__ import annotations

import urllib.error
import urllib.request
from contextlib import contextmanager
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from app.libs.probes.constants import ProbeIssueCode
from app.libs.probes.probe_runner import DefaultProbeRunner, _probe_urlopen
from app.libs.probes.results import ServiceProbeResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runner() -> DefaultProbeRunner:
    return DefaultProbeRunner(
        runtime=MagicMock(),
        topology=MagicMock(),
    )


def _mock_http_response(status_code: int):
    """Return a context-manager-compatible fake urllib response."""
    resp = MagicMock()
    resp.status = status_code
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# check_service_http unit tests
# ---------------------------------------------------------------------------

class TestCheckServiceHttp:
    def test_200_returns_healthy(self):
        runner = _make_runner()
        with patch("app.libs.probes.probe_runner._probe_urlopen", return_value=_mock_http_response(200)):
            result = runner.check_service_http(workspace_ip="10.0.0.1", port=8080)
        assert result.healthy is True
        assert result.issues == ()

    def test_302_redirect_returns_healthy(self):
        runner = _make_runner()
        with patch("app.libs.probes.probe_runner._probe_urlopen", return_value=_mock_http_response(302)):
            result = runner.check_service_http(workspace_ip="10.0.0.1", port=8080)
        assert result.healthy is True

    def test_201_returns_healthy(self):
        runner = _make_runner()
        with patch("app.libs.probes.probe_runner._probe_urlopen", return_value=_mock_http_response(201)):
            result = runner.check_service_http(workspace_ip="10.0.0.1", port=8080)
        assert result.healthy is True

    def test_500_returns_not_healthy(self):
        runner = _make_runner()
        http_err = urllib.error.HTTPError(
            url="http://10.0.0.1:8080/",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=None,
        )
        with patch("app.libs.probes.probe_runner._probe_urlopen", side_effect=http_err):
            result = runner.check_service_http(workspace_ip="10.0.0.1", port=8080)
        assert result.healthy is False
        assert any(i.code == ProbeIssueCode.SERVICE_HTTP_NOT_READY.value for i in result.issues)

    def test_404_returns_not_healthy(self):
        runner = _make_runner()
        http_err = urllib.error.HTTPError(
            url="http://10.0.0.1:8080/",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=None,
        )
        with patch("app.libs.probes.probe_runner._probe_urlopen", side_effect=http_err):
            result = runner.check_service_http(workspace_ip="10.0.0.1", port=8080)
        assert result.healthy is False

    def test_connection_refused_returns_not_healthy(self):
        runner = _make_runner()
        url_err = urllib.error.URLError("Connection refused")
        with patch("app.libs.probes.probe_runner._probe_urlopen", side_effect=url_err):
            result = runner.check_service_http(workspace_ip="10.0.0.1", port=8080)
        assert result.healthy is False
        assert any(i.code == ProbeIssueCode.SERVICE_HTTP_NOT_READY.value for i in result.issues)

    def test_timeout_returns_not_healthy(self):
        runner = _make_runner()
        with patch("app.libs.probes.probe_runner._probe_urlopen", side_effect=TimeoutError("timed out")):
            result = runner.check_service_http(workspace_ip="10.0.0.1", port=8080)
        assert result.healthy is False
        assert any("failed" in i.message.lower() for i in result.issues)

    def test_os_error_returns_not_healthy(self):
        runner = _make_runner()
        with patch("app.libs.probes.probe_runner._probe_urlopen", side_effect=OSError("network unreachable")):
            result = runner.check_service_http(workspace_ip="10.0.0.1", port=8080)
        assert result.healthy is False

    def test_result_includes_workspace_ip(self):
        runner = _make_runner()
        with patch("app.libs.probes.probe_runner._probe_urlopen", return_value=_mock_http_response(200)):
            result = runner.check_service_http(workspace_ip="192.168.1.50", port=8080)
        assert result.workspace_ip == "192.168.1.50"
        assert result.port == 8080

    def test_timeout_parameter_forwarded_to_urlopen(self):
        runner = _make_runner()
        captured_kwargs = {}

        def _fake_urlopen(req, timeout=None):
            captured_kwargs["timeout"] = timeout
            return _mock_http_response(200)

        with patch("app.libs.probes.probe_runner._probe_urlopen", side_effect=_fake_urlopen):
            runner.check_service_http(workspace_ip="10.0.0.1", port=8080, timeout_seconds=7.0)
        assert captured_kwargs["timeout"] == 7.0


# ---------------------------------------------------------------------------
# check_workspace_health: HTTP probe integration
# ---------------------------------------------------------------------------

class TestCheckWorkspaceHealthHttpIntegration:
    """check_workspace_health must call check_service_http after TCP succeeds."""

    def _make_runner_with_healthy_tcp(self) -> DefaultProbeRunner:
        runner = _make_runner()
        runner.check_container_running = MagicMock(
            return_value=MagicMock(healthy=True, container_state="running", issues=())
        )
        runner.check_topology_state = MagicMock(
            return_value=MagicMock(
                healthy=True,
                workspace_ip="10.0.0.1",
                internal_endpoint="10.0.0.1:8080",
                workspace_id=1,
                issues=(),
            )
        )
        runner.check_service_reachable = MagicMock(
            return_value=ServiceProbeResult(healthy=True, workspace_ip="10.0.0.1", port=8080, latency_ms=1.0)
        )
        return runner

    def test_healthy_when_tcp_and_http_both_pass(self):
        runner = self._make_runner_with_healthy_tcp()
        with patch("app.libs.probes.probe_runner._probe_urlopen", return_value=_mock_http_response(200)):
            result = runner.check_workspace_health(
                workspace_id="1",
                topology_id="1",
                node_id="node-1",
                container_id="ctr-abc",
            )
        assert result.healthy is True
        assert result.service_healthy is True

    def test_not_healthy_when_tcp_passes_but_http_fails(self):
        runner = self._make_runner_with_healthy_tcp()
        http_err = urllib.error.URLError("Connection refused")
        with patch("app.libs.probes.probe_runner._probe_urlopen", side_effect=http_err):
            result = runner.check_workspace_health(
                workspace_id="1",
                topology_id="1",
                node_id="node-1",
                container_id="ctr-abc",
            )
        assert result.healthy is False
        assert result.service_healthy is False
        assert any(i.code == ProbeIssueCode.SERVICE_HTTP_NOT_READY.value for i in result.issues)

    def test_not_healthy_when_tcp_fails_http_not_called(self):
        """When TCP fails, HTTP check must be skipped entirely."""
        runner = _make_runner()
        runner.check_container_running = MagicMock(
            return_value=MagicMock(healthy=True, container_state="running", issues=())
        )
        runner.check_topology_state = MagicMock(
            return_value=MagicMock(
                healthy=True,
                workspace_ip="10.0.0.1",
                internal_endpoint="10.0.0.1:8080",
                workspace_id=1,
                issues=(),
            )
        )
        runner.check_service_reachable = MagicMock(
            return_value=ServiceProbeResult(
                healthy=False,
                workspace_ip="10.0.0.1",
                port=8080,
                latency_ms=None,
                issues=(
                    MagicMock(code=ProbeIssueCode.SERVICE_TIMEOUT.value, component="service",
                              message="timed out", severity="ERROR"),
                ),
            )
        )

        http_called = {"called": False}

        def _should_not_be_called(*a, **kw):
            http_called["called"] = True
            return _mock_http_response(200)

        with patch("app.libs.probes.probe_runner._probe_urlopen", side_effect=_should_not_be_called):
            result = runner.check_workspace_health(
                workspace_id="1",
                topology_id="1",
                node_id="node-1",
                container_id="ctr-abc",
            )
        assert http_called["called"] is False
        assert result.healthy is False
        assert result.service_healthy is False

    def test_healthy_when_tcp_passes_and_http_probe_disabled_via_settings(self):
        """Integration/system tests disable DEVNEST_WORKSPACE_HTTP_PROBE_ENABLED; HTTP must not run."""
        runner = self._make_runner_with_healthy_tcp()
        http_called = {"n": 0}

        def _count_http(*_a, **_kw):
            http_called["n"] += 1
            return _mock_http_response(200)

        settings = MagicMock()
        settings.devnest_workspace_http_probe_enabled = False
        with patch("app.libs.common.config.get_settings", return_value=settings):
            with patch("app.libs.probes.probe_runner._probe_urlopen", side_effect=_count_http):
                result = runner.check_workspace_health(
                    workspace_id="1",
                    topology_id="1",
                    node_id="node-1",
                    container_id="ctr-abc",
                )
        assert http_called["n"] == 0
        assert result.healthy is True
        assert result.service_healthy is True

    def test_http_uses_execution_host_curl_when_reachability_runner_set(self):
        """EC2/SSH/SSM: HTTP readiness must not use urllib from the control plane."""
        recorded: list[list[str]] = []

        class _RemoteRunner:
            def run(self, cmd: list[str]) -> str:
                recorded.append(list(cmd))
                return ""

        runner = DefaultProbeRunner(
            runtime=MagicMock(),
            topology=MagicMock(),
            service_reachability_runner=_RemoteRunner(),
        )
        runner.check_container_running = MagicMock(
            return_value=MagicMock(healthy=True, container_state="running", issues=()),
        )
        runner.check_topology_state = MagicMock(
            return_value=MagicMock(
                healthy=True,
                workspace_ip="10.0.0.5",
                internal_endpoint="10.0.0.5:8080",
                workspace_id=1,
                issues=(),
            ),
        )
        settings = MagicMock()
        settings.devnest_workspace_http_probe_enabled = True
        settings.devnest_probe_assume_colocated_engine = True
        with patch("app.libs.common.config.get_settings", return_value=settings):
            with patch("app.libs.probes.probe_runner._probe_urlopen") as uo:
                result = runner.check_workspace_health(
                    workspace_id="1",
                    topology_id="1",
                    node_id="node-1",
                    container_id="ctr-abc",
                )
        assert result.healthy is True
        uo.assert_not_called()
        assert any("curl" in c for c in recorded), recorded


# ---------------------------------------------------------------------------
# ProbeRunner ABC: default check_service_http is a pass-through
# ---------------------------------------------------------------------------

class TestProbeRunnerAbcDefault:
    def test_abstract_base_default_returns_healthy(self):
        """The ABC concrete default must return healthy=True (backward-compat pass-through)."""
        from app.libs.probes.interfaces import ProbeRunner

        class _MinimalRunner(ProbeRunner):
            def check_container_running(self, *, container_id):
                return MagicMock()

            def check_topology_state(self, *, topology_id, node_id, workspace_id, expected_port=8080):
                return MagicMock()

            def check_service_reachable(self, *, workspace_ip, port=8080, timeout_seconds=2.0):
                return MagicMock()

            def check_workspace_health(
                self, *, workspace_id, topology_id, node_id, container_id, expected_port=8080, timeout_seconds=2.0
            ):
                return MagicMock()

        runner = _MinimalRunner()
        result = runner.check_service_http(workspace_ip="10.0.0.1", port=8080)
        assert result.healthy is True
