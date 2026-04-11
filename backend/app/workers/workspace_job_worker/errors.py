"""Errors for the workspace job worker."""


class WorkspaceJobWorkerError(Exception):
    """Unexpected worker failure (programming error, broken invariant, or non-orchestrator bug)."""

    pass


class UnsupportedWorkspaceJobTypeError(WorkspaceJobWorkerError):
    """No executor mapping for a ``WorkspaceJob.job_type`` value."""

    pass
