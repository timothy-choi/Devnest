"""Orchestrator service contract (workspace lifecycle coordination)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .results import WorkspaceBringUpResult


class OrchestratorService(ABC):
    @abstractmethod
    def bring_up_workspace_runtime(
        self,
        *,
        workspace_id: str,
        requested_config_version: int | None = None,
    ) -> WorkspaceBringUpResult:
        """Provision or start a workspace runtime."""

    def stop_workspace_runtime(self, *, workspace_id: str) -> None:
        """Stop a workspace container without removing it."""
        raise NotImplementedError

    def delete_workspace_runtime(self, *, workspace_id: str) -> None:
        """Remove workspace container and release runtime resources."""
        raise NotImplementedError

    def restart_workspace_runtime(self, *, workspace_id: str) -> None:
        """Restart the workspace container."""
        raise NotImplementedError

    def update_workspace_runtime(self, *, workspace_id: str) -> None:
        """Apply configuration or image updates to a workspace runtime."""
        raise NotImplementedError

    def check_workspace_runtime_health(self, *, workspace_id: str) -> WorkspaceBringUpResult:
        """Read-only health snapshot (no repair)."""
        raise NotImplementedError
