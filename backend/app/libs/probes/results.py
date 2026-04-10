"""Structured outcomes for read-only workspace probes (no side effects on success paths)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class HealthIssueSeverity(str, Enum):
    """Rough ordering for display and alerting; probe layer does not act on severity."""

    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


@dataclass(frozen=True)
class HealthIssue:
    """Single verification finding; prefer stable ``code`` values from ``ProbeIssueCode``."""

    code: str
    component: str
    message: str
    severity: HealthIssueSeverity


@dataclass(frozen=True)
class ContainerProbeResult:
    """Runtime/container inspection outcome for one workspace."""

    healthy: bool
    container_id: str
    container_state: str | None
    issues: tuple[HealthIssue, ...] = ()


@dataclass(frozen=True)
class TopologyProbeResult:
    """Topology runtime + attachment checks for one workspace on a node."""

    healthy: bool
    topology_id: int
    node_id: str
    workspace_id: int
    workspace_ip: str | None
    internal_endpoint: str | None
    issues: tuple[HealthIssue, ...] = ()


@dataclass(frozen=True)
class ServiceProbeResult:
    """Reachability check for the in-workspace service (e.g. IDE port)."""

    healthy: bool
    workspace_ip: str | None
    port: int
    latency_ms: float | None
    issues: tuple[HealthIssue, ...] = ()


@dataclass(frozen=True)
class WorkspaceHealthResult:
    """Roll-up of container, topology, and service probes for one workspace."""

    workspace_id: int
    healthy: bool
    runtime_healthy: bool
    topology_healthy: bool
    service_healthy: bool
    container_state: str | None
    workspace_ip: str | None
    internal_endpoint: str | None
    issues: tuple[HealthIssue, ...] = ()
