"""
Default ``ProbeRunner`` implementation: read-only checks via injected adapters.

All ``ProbeRunner`` methods are implemented: granular checks plus aggregate ``check_workspace_health``.

``HealthIssue.component``: ``runtime`` / ``topology`` / ``service`` reflect subsystem checks;
``probe`` marks invalid probe parameters or adapter exceptions surfaced as ``PROBE_EXECUTION_FAILED``.
"""

from __future__ import annotations

import errno
import math
import socket
import time

import ipaddress

from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.topology.errors import AttachmentHealthCheckError, TopologyHealthCheckError
from app.libs.topology.interfaces import TopologyAdapter
from app.libs.topology.system.command_runner import CommandRunner

from .constants import ProbeIssueCode
from .interfaces import ProbeRunner
from .results import (
    ContainerProbeResult,
    HealthIssue,
    HealthIssueSeverity,
    ServiceProbeResult,
    TopologyProbeResult,
    WorkspaceHealthResult,
)

# Tests patch this symbol. Patching ``probe_runner.socket.create_connection`` mutates the stdlib
# ``socket`` module (``probe_runner.socket`` is that module) and breaks unrelated TCP clients (httpx).
_probe_create_connection = socket.create_connection


def _parse_non_negative_int(raw: str) -> int | None:
    try:
        v = int(str(raw).strip(), 10)
    except (TypeError, ValueError):
        return None
    if v < 0:
        return None
    return v


def _endpoint_or_none(*, workspace_ip: str | None, port: int) -> str | None:
    ip = (workspace_ip or "").strip()
    if not ip:
        return None
    if port < 1 or port > 65535:
        return None
    return f"{ip}:{port}"


# Errnos treated as "refused / unreachable" for V1 TCP probe (platform-dependent sets overlap).
_OSR_UNREACHABLE_ERRNOS: frozenset[int] = frozenset(
    {
        errno.ECONNREFUSED,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.ENETDOWN,
        errno.EHOSTDOWN,
    },
)


def _service_issue(
    *,
    code: ProbeIssueCode,
    message: str,
) -> tuple[HealthIssue, ...]:
    return (
        HealthIssue(
            code=code.value,
            component="service",
            message=message,
            severity=HealthIssueSeverity.ERROR,
        ),
    )


class DefaultProbeRunner(ProbeRunner):
    """Probe runner backed by ``RuntimeAdapter`` and ``TopologyAdapter`` for inspection-only checks."""

    def __init__(
        self,
        *,
        runtime: RuntimeAdapter,
        topology: TopologyAdapter,
        service_reachability_runner: CommandRunner | None = None,
    ) -> None:
        self._runtime = runtime
        self._topology = topology
        self._service_reachability_runner = service_reachability_runner

    def check_container_running(
        self,
        *,
        container_id: str,
    ) -> ContainerProbeResult:
        cid = (container_id or "").strip()
        if not cid:
            return ContainerProbeResult(
                healthy=False,
                container_id="",
                container_state=None,
                issues=(
                    HealthIssue(
                        code=ProbeIssueCode.PROBE_EXECUTION_FAILED.value,
                        component="probe",
                        message="container_id is empty",
                        severity=HealthIssueSeverity.ERROR,
                    ),
                ),
            )

        try:
            ins = self._runtime.inspect_container(container_id=cid)
        except Exception as e:
            return ContainerProbeResult(
                healthy=False,
                container_id=cid,
                container_state=None,
                issues=(
                    HealthIssue(
                        code=ProbeIssueCode.RUNTIME_CONTAINER_STATE_UNKNOWN.value,
                        component="runtime",
                        message=f"inspect_container failed: {e}",
                        severity=HealthIssueSeverity.ERROR,
                    ),
                ),
            )

        if not ins.exists:
            return ContainerProbeResult(
                healthy=False,
                container_id=cid,
                container_state=ins.container_state,
                issues=(
                    HealthIssue(
                        code=ProbeIssueCode.RUNTIME_CONTAINER_MISSING.value,
                        component="runtime",
                        message="container does not exist",
                        severity=HealthIssueSeverity.ERROR,
                    ),
                ),
            )

        state = (ins.container_state or "").strip().lower()
        if state in ("", "unknown"):
            return ContainerProbeResult(
                healthy=False,
                container_id=ins.container_id or cid,
                container_state=ins.container_state,
                issues=(
                    HealthIssue(
                        code=ProbeIssueCode.RUNTIME_CONTAINER_STATE_UNKNOWN.value,
                        component="runtime",
                        message="container state is unknown or unreadable",
                        severity=HealthIssueSeverity.ERROR,
                    ),
                ),
            )

        if state != "running":
            return ContainerProbeResult(
                healthy=False,
                container_id=ins.container_id or cid,
                container_state=ins.container_state,
                issues=(
                    HealthIssue(
                        code=ProbeIssueCode.RUNTIME_NOT_RUNNING.value,
                        component="runtime",
                        message=f"container state is {ins.container_state!r}, expected running",
                        severity=HealthIssueSeverity.ERROR,
                    ),
                ),
            )

        return ContainerProbeResult(
            healthy=True,
            container_id=ins.container_id or cid,
            container_state=ins.container_state,
            issues=(),
        )

    def check_topology_state(
        self,
        *,
        topology_id: str,
        node_id: str,
        workspace_id: str,
        expected_port: int = 8080,
    ) -> TopologyProbeResult:
        nid = (node_id or "").strip()[:128]
        tid = _parse_non_negative_int(topology_id)
        wid = _parse_non_negative_int(workspace_id)

        def _bad_ids_result() -> TopologyProbeResult:
            return TopologyProbeResult(
                healthy=False,
                topology_id=tid if tid is not None else 0,
                workspace_id=wid if wid is not None else 0,
                node_id=nid,
                workspace_ip=None,
                internal_endpoint=None,
                issues=(
                    HealthIssue(
                        code=ProbeIssueCode.PROBE_EXECUTION_FAILED.value,
                        component="probe",
                        message="topology_id and workspace_id must be non-negative integers",
                        severity=HealthIssueSeverity.ERROR,
                    ),
                ),
            )

        if tid is None or wid is None:
            return _bad_ids_result()

        def _exec_failed(*, step: str, exc: BaseException) -> TopologyProbeResult:
            return TopologyProbeResult(
                healthy=False,
                topology_id=tid,
                node_id=nid,
                workspace_id=wid,
                workspace_ip=None,
                internal_endpoint=None,
                issues=(
                    HealthIssue(
                        code=ProbeIssueCode.PROBE_EXECUTION_FAILED.value,
                        component="probe",
                        message=f"{step} failed: {exc}",
                        severity=HealthIssueSeverity.ERROR,
                    ),
                ),
            )

        try:
            topo_res = self._topology.check_topology(topology_id=tid, node_id=nid)
        except TopologyHealthCheckError as e:
            return _exec_failed(step="check_topology", exc=e)
        except Exception as e:
            return _exec_failed(step="check_topology", exc=e)

        try:
            att_res = self._topology.check_attachment(
                topology_id=tid,
                node_id=nid,
                workspace_id=wid,
            )
        except AttachmentHealthCheckError as e:
            return _exec_failed(step="check_attachment", exc=e)
        except Exception as e:
            return _exec_failed(step="check_attachment", exc=e)

        issues: list[HealthIssue] = []

        if not topo_res.healthy:
            detail = "; ".join(topo_res.issues) if topo_res.issues else "topology runtime unhealthy"
            issues.append(
                HealthIssue(
                    code=ProbeIssueCode.TOPOLOGY_UNHEALTHY.value,
                    component="topology",
                    message=detail,
                    severity=HealthIssueSeverity.ERROR,
                ),
            )

        if not att_res.healthy:
            detail = "; ".join(att_res.issues) if att_res.issues else "attachment unhealthy"
            issues.append(
                HealthIssue(
                    code=ProbeIssueCode.TOPOLOGY_ATTACHMENT_MISSING.value,
                    component="topology",
                    message=detail,
                    severity=HealthIssueSeverity.ERROR,
                ),
            )

        ws_ip = (att_res.workspace_ip or "").strip() or None
        if not ws_ip:
            issues.append(
                HealthIssue(
                    code=ProbeIssueCode.TOPOLOGY_WORKSPACE_IP_MISSING.value,
                    component="topology",
                    message="workspace_ip is not set on attachment or lease",
                    severity=HealthIssueSeverity.ERROR,
                ),
            )

        internal_endpoint = _endpoint_or_none(workspace_ip=ws_ip, port=expected_port)
        if internal_endpoint is None and ws_ip:
            issues.append(
                HealthIssue(
                    code=ProbeIssueCode.TOPOLOGY_INTERNAL_ENDPOINT_MISSING.value,
                    component="topology",
                    message=f"cannot derive internal endpoint (invalid expected_port={expected_port})",
                    severity=HealthIssueSeverity.ERROR,
                ),
            )
        elif internal_endpoint is None and not ws_ip:
            issues.append(
                HealthIssue(
                    code=ProbeIssueCode.TOPOLOGY_INTERNAL_ENDPOINT_MISSING.value,
                    component="topology",
                    message="cannot derive internal endpoint without workspace_ip",
                    severity=HealthIssueSeverity.ERROR,
                ),
            )

        healthy = len(issues) == 0
        return TopologyProbeResult(
            healthy=healthy,
            topology_id=tid,
            node_id=nid,
            workspace_id=wid,
            workspace_ip=ws_ip,
            internal_endpoint=internal_endpoint,
            issues=tuple(issues),
        )

    def _check_service_reachable_via_runner(
        self,
        *,
        workspace_ip: str,
        port: int,
        timeout_seconds: float,
    ) -> ServiceProbeResult:
        """TCP check from the execution host (SSH) using ``nc``; IPv4 only."""
        ip = (workspace_ip or "").strip()
        try:
            ipaddress.IPv4Address(ip)
        except ValueError:
            return ServiceProbeResult(
                healthy=False,
                workspace_ip=ip or None,
                port=port,
                latency_ms=None,
                issues=_service_issue(
                    code=ProbeIssueCode.SERVICE_CONNECT_ERROR,
                    message=(
                        "remote service probe requires IPv4 workspace_ip "
                        f"(got {workspace_ip!r}); extend probe for IPv6 or use local routing"
                    ),
                ),
            )
        w = max(1, min(60, int(math.ceil(timeout_seconds))))
        runner = self._service_reachability_runner
        assert runner is not None
        t0 = time.perf_counter()
        try:
            runner.run(["timeout", str(w), "nc", "-z", ip, str(port)])
        except RuntimeError as e:
            return ServiceProbeResult(
                healthy=False,
                workspace_ip=ip,
                port=port,
                latency_ms=None,
                issues=_service_issue(
                    code=ProbeIssueCode.SERVICE_UNREACHABLE,
                    message=f"remote nc probe failed for {ip!r}:{port}: {e}",
                ),
            )
        t1 = time.perf_counter()
        return ServiceProbeResult(
            healthy=True,
            workspace_ip=ip,
            port=port,
            latency_ms=(t1 - t0) * 1000.0,
            issues=(),
        )

    def check_service_reachable(
        self,
        *,
        workspace_ip: str,
        port: int = 8080,
        timeout_seconds: float = 2.0,
    ) -> ServiceProbeResult:
        ip = (workspace_ip or "").strip()
        if not ip:
            return ServiceProbeResult(
                healthy=False,
                workspace_ip=None,
                port=port,
                latency_ms=None,
                issues=_service_issue(
                    code=ProbeIssueCode.SERVICE_CONNECT_ERROR,
                    message="workspace_ip is empty",
                ),
            )
        if port < 1 or port > 65535:
            return ServiceProbeResult(
                healthy=False,
                workspace_ip=ip,
                port=port,
                latency_ms=None,
                issues=_service_issue(
                    code=ProbeIssueCode.SERVICE_CONNECT_ERROR,
                    message=f"invalid port: {port}",
                ),
            )
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            return ServiceProbeResult(
                healthy=False,
                workspace_ip=ip,
                port=port,
                latency_ms=None,
                issues=_service_issue(
                    code=ProbeIssueCode.SERVICE_CONNECT_ERROR,
                    message=f"timeout_seconds must be a finite positive value, got {timeout_seconds!r}",
                ),
            )

        if self._service_reachability_runner is not None:
            return self._check_service_reachable_via_runner(
                workspace_ip=ip,
                port=port,
                timeout_seconds=timeout_seconds,
            )

        sock: socket.socket | None = None
        try:
            t0 = time.perf_counter()
            sock = _probe_create_connection((ip, port), timeout=timeout_seconds)
            t1 = time.perf_counter()
            latency_ms = (t1 - t0) * 1000.0
            return ServiceProbeResult(
                healthy=True,
                workspace_ip=ip,
                port=port,
                latency_ms=latency_ms,
                issues=(),
            )
        except (socket.timeout, TimeoutError):
            return ServiceProbeResult(
                healthy=False,
                workspace_ip=ip,
                port=port,
                latency_ms=None,
                issues=_service_issue(
                    code=ProbeIssueCode.SERVICE_TIMEOUT,
                    message=f"TCP connect to {ip!r}:{port} timed out after {timeout_seconds}s",
                ),
            )
        except ConnectionRefusedError:
            return ServiceProbeResult(
                healthy=False,
                workspace_ip=ip,
                port=port,
                latency_ms=None,
                issues=_service_issue(
                    code=ProbeIssueCode.SERVICE_UNREACHABLE,
                    message=f"TCP connect to {ip!r}:{port} refused",
                ),
            )
        except OSError as e:
            if isinstance(e, socket.gaierror):
                return ServiceProbeResult(
                    healthy=False,
                    workspace_ip=ip,
                    port=port,
                    latency_ms=None,
                    issues=_service_issue(
                        code=ProbeIssueCode.SERVICE_CONNECT_ERROR,
                        message=f"TCP connect address resolution failed for {ip!r}:{port}: {e}",
                    ),
                )
            eno = getattr(e, "errno", None)
            if eno in _OSR_UNREACHABLE_ERRNOS:
                return ServiceProbeResult(
                    healthy=False,
                    workspace_ip=ip,
                    port=port,
                    latency_ms=None,
                    issues=_service_issue(
                        code=ProbeIssueCode.SERVICE_UNREACHABLE,
                        message=f"TCP connect to {ip!r}:{port} unreachable: {e}",
                    ),
                )
            return ServiceProbeResult(
                healthy=False,
                workspace_ip=ip,
                port=port,
                latency_ms=None,
                issues=_service_issue(
                    code=ProbeIssueCode.SERVICE_CONNECT_ERROR,
                    message=f"TCP connect to {ip!r}:{port} failed: {e}",
                ),
            )
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def check_workspace_health(
        self,
        *,
        workspace_id: str,
        topology_id: str,
        node_id: str,
        container_id: str,
        expected_port: int = 8080,
        timeout_seconds: float = 2.0,
    ) -> WorkspaceHealthResult:
        ctr = self.check_container_running(container_id=container_id)
        topo = self.check_topology_state(
            topology_id=topology_id,
            node_id=node_id,
            workspace_id=workspace_id,
            expected_port=expected_port,
        )

        ws_ip = (topo.workspace_ip or "").strip()
        if ws_ip:
            svc = self.check_service_reachable(
                workspace_ip=ws_ip,
                port=expected_port,
                timeout_seconds=timeout_seconds,
            )
            service_healthy = svc.healthy
            service_issues = svc.issues
        else:
            service_healthy = False
            missing_ip_code = ProbeIssueCode.TOPOLOGY_WORKSPACE_IP_MISSING.value
            if any(i.code == missing_ip_code for i in topo.issues):
                # Topology probe already reported missing IP; avoid duplicate roll-up issue.
                service_issues: tuple[HealthIssue, ...] = ()
            else:
                service_issues = (
                    HealthIssue(
                        code=missing_ip_code,
                        component="topology",
                        message="service probe skipped: no workspace_ip from topology probe",
                        severity=HealthIssueSeverity.ERROR,
                    ),
                )

        all_issues = (*ctr.issues, *topo.issues, *service_issues)
        runtime_healthy = ctr.healthy
        topology_healthy = topo.healthy
        healthy = runtime_healthy and topology_healthy and service_healthy

        return WorkspaceHealthResult(
            workspace_id=topo.workspace_id,
            healthy=healthy,
            runtime_healthy=runtime_healthy,
            topology_healthy=topology_healthy,
            service_healthy=service_healthy,
            container_state=ctr.container_state,
            workspace_ip=topo.workspace_ip,
            internal_endpoint=topo.internal_endpoint,
            issues=all_issues,
        )
