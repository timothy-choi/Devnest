"""V1 topology: persistence models, operation results, errors, and ``TopologyAdapter`` contract."""

from .errors import (
    AttachmentHealthCheckError,
    TopologyDeleteError,
    TopologyError,
    TopologyHealthCheckError,
    TopologyRuntimeCreateError,
    TopologyRuntimeNotFoundError,
    WorkspaceAttachmentError,
    WorkspaceDetachError,
    WorkspaceIPAllocationError,
)
from .db_topology_adapter import DbTopologyAdapter
from .interfaces import TopologyAdapter
from .models import (
    IpAllocation,
    Topology,
    TopologyAttachment,
    TopologyAttachmentStatus,
    TopologyRuntime,
    TopologyRuntimeStatus,
)
from .results import (
    AllocateWorkspaceIPResult,
    AttachWorkspaceResult,
    CheckAttachmentResult,
    CheckTopologyResult,
    DetachWorkspaceResult,
    EnsureNodeTopologyResult,
    TopologyJanitorResult,
)

__all__ = [
    "AllocateWorkspaceIPResult",
    "AttachWorkspaceResult",
    "AttachmentHealthCheckError",
    "CheckAttachmentResult",
    "CheckTopologyResult",
    "DbTopologyAdapter",
    "DetachWorkspaceResult",
    "EnsureNodeTopologyResult",
    "TopologyJanitorResult",
    "IpAllocation",
    "Topology",
    "TopologyAdapter",
    "TopologyAttachment",
    "TopologyAttachmentStatus",
    "TopologyDeleteError",
    "TopologyError",
    "TopologyHealthCheckError",
    "TopologyRuntime",
    "TopologyRuntimeCreateError",
    "TopologyRuntimeNotFoundError",
    "TopologyRuntimeStatus",
    "WorkspaceAttachmentError",
    "WorkspaceDetachError",
    "WorkspaceIPAllocationError",
]
