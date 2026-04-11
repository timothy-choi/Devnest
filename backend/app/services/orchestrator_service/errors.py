"""Orchestrator service exceptions."""


class OrchestratorError(Exception):
    """Base exception for orchestrator failures."""

    pass


class WorkspaceBringUpError(OrchestratorError):
    """Raised when workspace bring-up fails."""

    pass


class WorkspaceStopError(OrchestratorError):
    """Raised when workspace stop flow fails unexpectedly."""

    pass


class WorkspaceDeleteError(OrchestratorError):
    """Raised when workspace delete flow fails unexpectedly."""

    pass


class WorkspaceRestartError(OrchestratorError):
    """Raised when the restart flow hits an unexpected failure (validation, inspect, etc.)."""

    pass


class WorkspaceUpdateError(OrchestratorError):
    """Raised when the update flow hits an unexpected failure (validation, inspect, etc.)."""

    pass
