"""Protocol for snapshot blob storage (metadata URI ↔ concrete location)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SnapshotStorageProvider(Protocol):
    """Maps snapshot identity to a host-local archive path and stable ``file://`` URI."""

    def archive_path(self, *, workspace_id: int, snapshot_id: int) -> str:
        """Absolute path to the ``.tar.gz`` archive (may not exist yet)."""

    def storage_uri(self, *, workspace_id: int, snapshot_id: int) -> str:
        """Opaque URI persisted on :class:`~app.services.workspace_service.models.WorkspaceSnapshot`."""

    def delete_archive(self, *, workspace_id: int, snapshot_id: int) -> None:
        """Remove the archive if present; no-op when missing."""

    def has_nonempty_archive(self, *, workspace_id: int, snapshot_id: int) -> bool:
        """True when a readable archive exists with size > 0 (restore preflight)."""
