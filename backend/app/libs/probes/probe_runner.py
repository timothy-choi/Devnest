"""
Default ``ProbeRunner`` implementation: read-only checks via injected adapters.

``check_container_running`` and ``check_topology_state`` are implemented; service and aggregate
workspace probes are not yet.
"""

from __future__ import annotations

from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.topology.errors import AttachmentHealthCheckError, TopologyHealthCheckError
from app.libs.topology.interfaces import TopologyAdapter

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


class DefaultProbeRunner(ProbeRunner):
    """Probe runner backed by ``RuntimeAdapter`` and ``TopologyAdapter`` for inspection-only checks."""

    def __init__(self, *, runtime: RuntimeAdapter, topology: TopologyAdapter) -> None:
        self._runtime = runtime
        self._topology = topology

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
                        code=ProbeIssueCode.RUNTIME_CONTAINER_STATE_UNKNOWN.value,
                        component="runtime",
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
                        component="topology",
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
                        component="topology",
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

    def check_service_reachable(
        self,
        *,
        workspace_ip: str,
        port: int = 8080,
        timeout_seconds: float = 2.0,
    ) -> ServiceProbeResult:
        raise NotImplementedError("check_service_reachable is not implemented yet")

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
        raise NotImplementedError("check_workspace_health is not implemented yet")
