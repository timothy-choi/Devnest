"""Orchestrator service exceptions."""


class OrchestratorError(Exception):
    """Base for unexpected orchestrator failures (validation, inspect, or non-result errors)."""

    pass


class AppOrchestratorBindingError(OrchestratorError):
    """Cannot build a process-local orchestrator (e.g. Docker engine unreachable)."""

    pass


class WorkspaceBringUpError(OrchestratorError):
    """Unexpected bring-up failure: validation, runtime, topology, or probe errors (not probe-unhealthy roll-ups)."""

    pass


class WorkspaceStopError(OrchestratorError):
    """Unexpected stop failure: validation, inspect, or non-adapter errors during detach/stop."""

    pass


class WorkspaceDeleteError(OrchestratorError):
    """Unexpected delete failure: validation, inspect, or non-adapter errors during detach/delete/topology."""

    pass


class WorkspaceRestartError(OrchestratorError):
    """Unexpected restart failure: validation, inspect, or wrapped stop errors that abort the flow."""

    pass


class WorkspaceUpdateError(OrchestratorError):
    """Unexpected update failure: validation, inspect, probe errors in noop path, or wrapped restart errors."""

    pass


class WorkspaceSnapshotError(OrchestratorError):
    """Snapshot export/import failed (validation, I/O, or unsafe archive contents)."""

    pass
