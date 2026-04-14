"""Orchestrator service contract (workspace lifecycle coordination)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .results import (
    WorkspaceBringUpResult,
    WorkspaceDeleteResult,
    WorkspaceRestartResult,
    WorkspaceSnapshotOperationResult,
    WorkspaceStopResult,
    WorkspaceUpdateResult,
)


class OrchestratorService(ABC):
    @abstractmethod
    def bring_up_workspace_runtime(
        self,
        *,
        workspace_id: str,
        requested_config_version: int | None = None,
        cpu_limit_cores: float | None = None,
        memory_limit_mib: int | None = None,
        env: dict | None = None,
        features: dict | None = None,
    ) -> WorkspaceBringUpResult:
        """Provision or start a workspace runtime.

        ``cpu_limit_cores`` and ``memory_limit_mib`` enforce container resource quotas when set.
        ``env`` injects additional environment variables from ``config_json``.
        ``features`` passes optional feature flags (e.g. ``terminal_enabled``) so the orchestrator
        can configure the runtime accordingly.
        """

    @abstractmethod
    def stop_workspace_runtime(
        self,
        *,
        workspace_id: str,
        container_id: str | None = None,
        requested_by: str | None = None,
    ) -> WorkspaceStopResult:
        """Detach topology and stop the workspace container.

        ``container_id`` should be the persisted engine ID from ``WorkspaceRuntime.container_id``
        when available. Falls back to deterministic name derivation when ``None``.
        """

    @abstractmethod
    def delete_workspace_runtime(
        self,
        *,
        workspace_id: str,
        container_id: str | None = None,
        requested_by: str | None = None,
    ) -> WorkspaceDeleteResult:
        """Detach topology, delete the workspace container, optionally remove node topology if safe.

        ``container_id`` should be the persisted engine ID from ``WorkspaceRuntime.container_id``
        when available. Falls back to deterministic name derivation when ``None``.
        """

    @abstractmethod
    def restart_workspace_runtime(
        self,
        *,
        workspace_id: str,
        container_id: str | None = None,
        requested_by: str | None = None,
        requested_config_version: int | None = None,
    ) -> WorkspaceRestartResult:
        """Stop then bring the workspace runtime back up (controlled restart cycle).

        ``container_id`` should be the persisted engine ID from ``WorkspaceRuntime.container_id``
        when available; it is used for the stop phase only (the bring-up phase allocates a new ID).
        """

    @abstractmethod
    def update_workspace_runtime(
        self,
        *,
        workspace_id: str,
        container_id: str | None = None,
        requested_config_version: int,
        requested_by: str | None = None,
    ) -> WorkspaceUpdateResult:
        """Apply ``requested_config_version`` (no-op when already current, else restart-based V1).

        ``container_id`` should be the persisted engine ID from ``WorkspaceRuntime.container_id``
        when available.
        """

    @abstractmethod
    def check_workspace_runtime_health(
        self,
        *,
        workspace_id: str,
        container_id: str | None = None,
    ) -> WorkspaceBringUpResult:
        """Read-only probe roll-up for an existing workspace runtime (no repair, no topology mutation).

        ``container_id`` should be the persisted engine ID from ``WorkspaceRuntime.container_id``
        when available. Falls back to deterministic name derivation when ``None``.
        """

    @abstractmethod
    def export_workspace_filesystem_snapshot(
        self,
        *,
        workspace_id: str,
        archive_path: str,
    ) -> WorkspaceSnapshotOperationResult:
        """Write a compressed archive of the workspace project directory to ``archive_path``."""

    @abstractmethod
    def import_workspace_filesystem_snapshot(
        self,
        *,
        workspace_id: str,
        archive_path: str,
    ) -> WorkspaceSnapshotOperationResult:
        """Extract a snapshot archive into the workspace project directory (V1: overwrites files)."""
