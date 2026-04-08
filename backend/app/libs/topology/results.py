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
class CheckTopologyResult:
    """Outcome of ``check_topology``; ``healthy`` is False when runtime exists but is degraded per policy."""

    healthy: bool
    status: TopologyRuntimeStatus
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckAttachmentResult:
    """Outcome of ``check_attachment``."""

    healthy: bool
    status: TopologyAttachmentStatus
    workspace_ip: str | None = None
    internal_endpoint: str | None = None
    issues: tuple[str, ...] = ()
