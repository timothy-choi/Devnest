"""Workspace control-plane enumerations (stored as strings in the database)."""

from enum import Enum


class WorkspaceStatus(str, Enum):
    """Transactional workspace lifecycle; runtime fields are owned by the orchestrator."""

    CREATING = "CREATING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    RESTARTING = "RESTARTING"
    UPDATING = "UPDATING"
    ERROR = "ERROR"
    DELETING = "DELETING"


class WorkspaceJobType(str, Enum):
    CREATE = "CREATE"
    START = "START"
    STOP = "STOP"
    RESTART = "RESTART"
    DELETE = "DELETE"
    UPDATE = "UPDATE"


class WorkspaceJobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkspaceRuntimeHealthStatus(str, Enum):
    """Observed health for ``WorkspaceRuntime`` (filled by orchestrator / probes later)."""

    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
