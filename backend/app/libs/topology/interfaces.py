"""Abstract topology adapter: node-local bridge/subnet, IP leases, workspace attachment (orchestrator-facing)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .results import (
    AllocateWorkspaceIPResult,
    AttachWorkspaceResult,
    CheckAttachmentResult,
    CheckTopologyResult,
    DetachWorkspaceResult,
    EnsureNodeTopologyResult,
    TopologyJanitorResult,
)


class TopologyAdapter(ABC):
    """
    Node-local networking abstraction between the orchestrator and the runtime adapter netns output.

    V1 assumptions (additive later): ``node_bridge`` mode, one **runtime row per (topology, node)**,
    stable **internal** ``workspace_ip``, and in-container service reachability at
    ``{workspace_ip}:8080`` (workspace IDE). Implementations may persist via ``models`` tables;
    callers own orchestration order: ensure runtime → allocate IP → attach → probes.

    This ABC defines contracts only; concrete adapters may perform node-local bridge setup (V1).
    """

    @abstractmethod
    def ensure_node_topology(self, *, topology_id: int, node_id: str) -> EnsureNodeTopologyResult:
        """
        Ensure the node-local topology runtime exists and is usable (bridge, CIDR, gateway as configured).

        Raises:
            TopologyRuntimeCreateError: Cannot create or reconcile runtime on the node.
        """

    @abstractmethod
    def allocate_workspace_ip(
        self,
        *,
        topology_id: int,
        node_id: str,
        workspace_id: int,
    ) -> AllocateWorkspaceIPResult:
        """
        Allocate or reuse a stable internal IP for ``workspace_id`` in this topology on ``node_id``.

        Raises:
            TopologyRuntimeNotFoundError: Runtime not present; caller should run ``ensure_node_topology`` first.
            WorkspaceIPAllocationError: Runtime not READY, pool exhausted, or lease conflict.
        """

    @abstractmethod
    def attach_workspace(
        self,
        *,
        topology_id: int,
        node_id: str,
        workspace_id: int,
        container_id: str,
        netns_ref: str,
        workspace_ip: str,
    ) -> AttachWorkspaceResult:
        """
        Attach the running container (``netns_ref`` from the runtime adapter) to the topology bridge.

        Raises:
            TopologyRuntimeNotFoundError: No runtime for this topology on the node.
            WorkspaceAttachmentError: veth/bridge wiring or persistence failed.
        """

    @abstractmethod
    def detach_workspace(
        self,
        *,
        topology_id: int,
        node_id: str,
        workspace_id: int,
    ) -> DetachWorkspaceResult:
        """
        Detach ``workspace_id`` from the topology on ``node_id`` (V1: DB attachment state; IP lease kept).

        Idempotent: no attachment row returns ``detached=False`` with ``status`` ``DETACHED``.
        ``released_ip`` remains ``False`` in V1 unless a future step defines lease release on detach.

        Raises:
            WorkspaceDetachError: Persisting detach state failed.
        """

    @abstractmethod
    def release_workspace_ip_lease(
        self,
        *,
        topology_id: int,
        node_id: str,
        workspace_id: int,
    ) -> bool:
        """
        Mark the active IP allocation row for this workspace as released (``released_at`` set).

        Idempotent: no active lease returns ``False``. Used after failed bring-up / ERROR cleanup so
        addresses return to the pool while preserving history rows.

        Raises:
            WorkspaceIPAllocationError: Persisting the release failed (caller may retry).
        """

    @abstractmethod
    def delete_topology(self, *, topology_id: int, node_id: str) -> None:
        """
        Remove node-local topology runtime when no longer needed (attachments should be detached first).

        Raises:
            TopologyDeleteError: Deletion would be unsafe (e.g. active attachments) or Linux cleanup failed.

        ``DbTopologyAdapter`` treats a missing runtime as success (idempotent no-op).
        """

    @abstractmethod
    def check_topology(self, *, topology_id: int, node_id: str) -> CheckTopologyResult:
        """
        Verify topology runtime health: DB/runtime row plus optional live bridge checks (V1).

        ``DbTopologyAdapter`` prefixes issues with ``db:`` vs ``linux:`` when Linux checks run.
        Missing runtime yields ``healthy=False``; no exception for not-found.

        Raises:
            TopologyHealthCheckError: Implementation cannot read state (rare).
        """

    @abstractmethod
    def check_attachment(
        self,
        *,
        topology_id: int,
        node_id: str,
        workspace_id: int,
    ) -> CheckAttachmentResult:
        """
        Verify attachment health: DB row, lease vs ``workspace_ip``, optional host veth/bridge linkage.

        ``DbTopologyAdapter`` prefixes issues with ``db:`` vs ``linux:`` when Linux checks run.
        Missing attachment yields ``healthy=False``; no exception for not-found.

        Raises:
            AttachmentHealthCheckError: Implementation cannot read state (rare).
        """

    def run_topology_janitor(
        self,
        *,
        topology_id: int,
        node_id: str,
        stale_attaching_seconds: int = 600,
    ) -> TopologyJanitorResult:
        """Repair stuck attachments, leaked IP leases, and simple DB/workspace drift (default: noop)."""
        return TopologyJanitorResult()
