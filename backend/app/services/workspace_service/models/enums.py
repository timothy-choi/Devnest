"""Workspace control-plane enumerations (stored as strings in the database)."""

from enum import Enum


class WorkspaceStatus(str, Enum):
    """Control-plane lifecycle states stored on ``Workspace``.

    **Transactional (``*ING``):** set by the Workspace Service when accepting an intent and
    enqueueing a :class:`~app.services.workspace_service.models.WorkspaceJob`.

    **Settled:** updated by the job worker from orchestrator results (``RUNNING``, ``STOPPED``,
    ``DELETED``) or failures (``ERROR``). Design docs sometimes call the failure state *FAILED*;
    the canonical stored value here is ``ERROR``.
    """

    CREATING = "CREATING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    RESTARTING = "RESTARTING"
    UPDATING = "UPDATING"
    ERROR = "ERROR"
    DELETING = "DELETING"
    DELETED = "DELETED"


class WorkspaceJobType(str, Enum):
    CREATE = "CREATE"
    START = "START"
    STOP = "STOP"
    RESTART = "RESTART"
    DELETE = "DELETE"
    UPDATE = "UPDATE"
    RECONCILE_RUNTIME = "RECONCILE_RUNTIME"


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


class WorkspaceSessionStatus(str, Enum):
    """Lifecycle for :class:`~app.services.workspace_service.models.workspace_session.WorkspaceSession`."""

    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class WorkspaceSessionRole(str, Enum):
    """V1: only ``OWNER`` is issued; collaborators / org roles are deferred."""

    OWNER = "OWNER"
