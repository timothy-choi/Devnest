"""Orchestrator service contract (workspace lifecycle coordination)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .results import WorkspaceBringUpResult, WorkspaceDeleteResult, WorkspaceStopResult


class OrchestratorService(ABC):
    @abstractmethod
    def bring_up_workspace_runtime(
        self,
        *,
        workspace_id: str,
        requested_config_version: int | None = None,
    ) -> WorkspaceBringUpResult:
        """Provision or start a workspace runtime."""

    @abstractmethod
    def stop_workspace_runtime(
        self,
        *,
        workspace_id: str,
        requested_by: str | None = None,
    ) -> WorkspaceStopResult:
        """Detach topology and stop the workspace container."""

    @abstractmethod
    def delete_workspace_runtime(
        self,
        *,
        workspace_id: str,
        requested_by: str | None = None,
    ) -> WorkspaceDeleteResult:
        """Detach topology, delete the workspace container, optionally remove node topology if safe."""

    def restart_workspace_runtime(self, *, workspace_id: str) -> None:
        """Restart the workspace container."""
        raise NotImplementedError

    def update_workspace_runtime(self, *, workspace_id: str) -> None:
        """Apply configuration or image updates to a workspace runtime."""
        raise NotImplementedError

    def check_workspace_runtime_health(self, *, workspace_id: str) -> WorkspaceBringUpResult:
        """Read-only health snapshot (no repair)."""
        raise NotImplementedError
