"""Usage tracking event types."""

from enum import Enum


class UsageEventType(str, Enum):
    """Distinct usage signals tracked by the platform.

    Each value maps to one :class:`WorkspaceUsageRecord` row.
    """

    # Workspace lifecycle events
    WORKSPACE_CREATED = "workspace.created"
    WORKSPACE_STARTED = "workspace.started"
    WORKSPACE_STOPPED = "workspace.stopped"
    WORKSPACE_DELETED = "workspace.deleted"
    WORKSPACE_JOB_FAILED = "workspace.job.failed"

    # Attach / session
    SESSION_CREATED = "session.created"

    # Snapshots
    SNAPSHOT_CREATED = "snapshot.created"
    SNAPSHOT_RESTORED = "snapshot.restored"

    # Node / infrastructure
    NODE_PROVISIONED = "node.provisioned"
    NODE_TERMINATED = "node.terminated"

    # Autoscaler
    AUTOSCALER_SCALE_UP = "autoscaler.scale_up"
    AUTOSCALER_SCALE_DOWN = "autoscaler.scale_down"
