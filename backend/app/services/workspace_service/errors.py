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


class WorkspaceAccessDeniedError(WorkspaceServiceError):
    """Caller is authenticated but lacks a valid workspace session for access coordinates."""

    pass
