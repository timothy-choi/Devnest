"""Orchestrator service exceptions."""


class OrchestratorError(Exception):
    """Base for unexpected orchestrator failures (validation, inspect, or non-result errors)."""

    pass


class AppOrchestratorBindingError(OrchestratorError):
    """Cannot build a process-local orchestrator (e.g. Docker engine unreachable)."""

    pass


class WorkspaceBringUpError(OrchestratorError):
    """Unexpected bring-up failure: validation, runtime, topology, or probe errors (not probe-unhealthy roll-ups)."""

    def __init__(
        self,
        message: str,
        *,
        rollback_attempted: bool = False,
        rollback_succeeded: bool | None = None,
        rollback_issues: list[str] | None = None,
        rollback_container_id: str | None = None,
        rollback_container_state: str | None = None,
    ) -> None:
        super().__init__(message)
        self.rollback_attempted = rollback_attempted
        self.rollback_succeeded = rollback_succeeded
        self.rollback_issues = list(rollback_issues) if rollback_issues else None
        self.rollback_container_id = rollback_container_id
        self.rollback_container_state = rollback_container_state


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
