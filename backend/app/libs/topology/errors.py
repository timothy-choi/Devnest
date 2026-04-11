"""Topology-layer exceptions (orchestrator may map these to workspace/job errors)."""


class TopologyError(Exception):
    """Base class for failures surfaced by a ``TopologyAdapter`` implementation."""


class TopologyRuntimeCreateError(TopologyError):
    """Raised when ``ensure_node_topology`` cannot create or reconcile node-local runtime (bridge/subnet)."""


class TopologyRuntimeNotFoundError(TopologyError):
    """Raised when a topology runtime is required but missing on the node."""


class TopologyDeleteError(TopologyError):
    """Raised when ``delete_topology`` cannot remove node-local runtime safely (V1)."""


class WorkspaceIPAllocationError(TopologyError):
    """Raised when ``allocate_workspace_ip`` cannot reserve or reuse an address in the topology CIDR."""


class WorkspaceAttachmentError(TopologyError):
    """Raised when ``attach_workspace`` cannot join the container netns to the topology bridge."""


class WorkspaceDetachError(TopologyError):
    """Raised when ``detach_workspace`` cannot remove the workspace from the topology."""


class TopologyHealthCheckError(TopologyError):
    """Raised when ``check_topology`` cannot complete (e.g. agent unreachable); not used for unhealthy-but-known state."""


class AttachmentHealthCheckError(TopologyError):
    """Raised when ``check_attachment`` cannot complete (e.g. inspect failure)."""
