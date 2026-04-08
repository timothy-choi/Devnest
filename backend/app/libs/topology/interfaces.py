"""Abstract topology adapter: node-local bridge/subnet, IP leases, workspace attachment (orchestrator-facing)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .errors import (
    AttachmentHealthCheckError,
    TopologyHealthCheckError,
    TopologyRuntimeCreateError,
    TopologyRuntimeNotFoundError,
    WorkspaceAttachmentError,
    WorkspaceDetachError,
    WorkspaceIPAllocationError,
)
from .results import (
    AllocateWorkspaceIPResult,
    AttachWorkspaceResult,
    CheckAttachmentResult,
    CheckTopologyResult,
    EnsureNodeTopologyResult,
)


class TopologyAdapter(ABC):
    """
    Node-local networking abstraction between the orchestrator and the runtime adapter netns output.

    V1 assumptions (additive later): ``node_bridge`` mode, one **runtime row per (topology, node)**,
    stable **internal** ``workspace_ip``, and in-container service reachability at
    ``{workspace_ip}:8080`` (workspace IDE). Implementations may persist via ``models`` tables;
    callers own orchestration order: ensure runtime → allocate IP → attach → probes.

    This ABC defines contracts only; no Linux bridge/iptables execution lives here.
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
            WorkspaceIPAllocationError: Pool exhausted or lease conflict.
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
    def detach_workspace(self, *, topology_id: int, node_id: str, workspace_id: int) -> None:
        """
        Detach ``workspace_id`` from the topology on ``node_id`` (release veth / routing as needed).

        Raises:
            WorkspaceDetachError: Teardown failed (attachment may be partially removed).
        """

    @abstractmethod
    def delete_topology(self, *, topology_id: int, node_id: str) -> None:
        """
        Remove node-local topology runtime when no longer needed (attachments should be detached first).

        Raises:
            TopologyRuntimeNotFoundError: Nothing to delete (implementations may treat as no-op instead).
        """

    @abstractmethod
    def check_topology(self, *, topology_id: int, node_id: str) -> CheckTopologyResult:
        """
        Verify topology runtime health; returns structured status without raising for degraded-but-known state.

        Raises:
            TopologyHealthCheckError: Probe could not run (e.g. agent down).
            TopologyRuntimeNotFoundError: No runtime record on the node.
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
        Verify workspace attachment health (interfaces, address, reachability policy as implemented).

        Raises:
            AttachmentHealthCheckError: Check could not run.
        """
