"""Normalized return types for ``TopologyAdapter`` operations (not SQL persistence rows)."""

from __future__ import annotations

from dataclasses import dataclass

from .models.enums import TopologyAttachmentStatus, TopologyRuntimeStatus


@dataclass(frozen=True)
class EnsureNodeTopologyResult:
    """Outcome of ``ensure_node_topology`` (V1: one node-local bridge/subnet per topology runtime)."""

    topology_runtime_id: int
    bridge_name: str | None
    cidr: str | None
    gateway_ip: str | None
    status: TopologyRuntimeStatus


@dataclass(frozen=True)
class AllocateWorkspaceIPResult:
    """Outcome of ``allocate_workspace_ip`` (stable lease until released)."""

    workspace_ip: str
    leased_existing: bool


@dataclass(frozen=True)
class AttachWorkspaceResult:
    """Outcome of ``attach_workspace``; ``internal_endpoint`` is typically ``{workspace_ip}:8080`` for the IDE."""

    attachment_id: int
    workspace_ip: str
    bridge_name: str | None
    gateway_ip: str | None
    internal_endpoint: str


@dataclass(frozen=True)
class DetachWorkspaceResult:
    """Outcome of ``detach_workspace`` (V1: attachment row only; IP lease stays active unless explicitly released later)."""

    detached: bool
    status: TopologyAttachmentStatus
    workspace_id: int
    workspace_ip: str | None
    released_ip: bool


@dataclass(frozen=True)
class CheckTopologyResult:
    """Outcome of ``check_topology``; ``healthy`` is False when ``issues`` is non-empty (DB and/or live bridge)."""

    healthy: bool
    status: TopologyRuntimeStatus
    issues: tuple[str, ...] = ()
    topology_runtime_id: int | None = None
    bridge_name: str | None = None
    cidr: str | None = None
    gateway_ip: str | None = None


@dataclass(frozen=True)
class CheckAttachmentResult:
    """Outcome of ``check_attachment``; ``healthy`` is False when ``issues`` is non-empty (DB and/or live veth)."""

    healthy: bool
    status: TopologyAttachmentStatus
    workspace_ip: str | None = None
    internal_endpoint: str | None = None
    issues: tuple[str, ...] = ()
    attachment_id: int | None = None


@dataclass(frozen=True)
class TopologyJanitorResult:
    """Idempotent repair counts from :meth:`TopologyAdapter.run_topology_janitor`."""

    stale_attachments_cleaned: int = 0
    orphan_ip_leases_released: int = 0
    drift_repairs: int = 0
    issues: tuple[str, ...] = ()
