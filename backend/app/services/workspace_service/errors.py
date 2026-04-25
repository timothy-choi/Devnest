"""Workspace control-plane service exceptions (intent validation; no orchestrator)."""


class WorkspaceServiceError(Exception):
    """Base for workspace service failures."""

    pass


class WorkspaceNotFoundError(WorkspaceServiceError):
    """Workspace id missing or not visible to the requester."""

    pass


class WorkspaceBusyError(WorkspaceServiceError):
    """Workspace is in a transactional in-progress status."""

    pass


class WorkspaceInvalidStateError(WorkspaceServiceError):
    """Requested operation is not valid for the current workspace status."""

    pass


class WorkspaceGatewayUnavailableError(WorkspaceServiceError):
    """Gateway edge or upstream path is not ready for workspace access (transient infrastructure)."""

    pass


class WorkspaceAccessDeniedError(WorkspaceServiceError):
    """Caller is authenticated but lacks a valid workspace session for access coordinates."""

    pass


class SnapshotNotFoundError(WorkspaceServiceError):
    """Snapshot id missing or not visible to the requester."""

    pass


class SnapshotConflictError(WorkspaceServiceError):
    """Snapshot operation conflicts with current state or an in-flight job."""

    pass


class WorkspaceSchedulingCapacityError(WorkspaceServiceError):
    """No READY schedulable execution node can accept this workspace (capacity or resources)."""

    pass


class WorkspaceSchedulingInvalidError(WorkspaceServiceError):
    """Placement parameters are invalid for scheduling (e.g. non-positive resource request)."""

    pass
