"""
Default ``ProbeRunner`` implementation: read-only checks via injected adapters.

Only ``check_container_running`` is implemented; other methods raise ``NotImplementedError``
until topology/service probes are added.
"""

from __future__ import annotations

from app.libs.runtime.interfaces import RuntimeAdapter

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


class DefaultProbeRunner(ProbeRunner):
    """Probe runner backed by ``RuntimeAdapter`` (and later topology) for inspection-only checks."""

    def __init__(self, *, runtime: RuntimeAdapter) -> None:
        self._runtime = runtime

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
        raise NotImplementedError("check_topology_state is not implemented yet")

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
