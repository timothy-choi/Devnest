"""Local filesystem snapshot storage (single-node / dev V1)."""

from __future__ import annotations

import os
from pathlib import Path


class LocalFilesystemSnapshotStorage:
    """
    Stores archives under ``{root}/ws-{workspace_id}/snapshot-{snapshot_id}.tar.gz``.

    TODO: Add S3/EFS provider implementing :class:`SnapshotStorageProvider` for multi-node clusters.
    """

    def __init__(self, root_dir: str) -> None:
        self._root = Path(root_dir).expanduser().resolve()

    def archive_path(self, *, workspace_id: int, snapshot_id: int) -> str:
        d = self._root / f"ws-{int(workspace_id)}"
        d.mkdir(parents=True, exist_ok=True)
        return str(d / f"snapshot-{int(snapshot_id)}.tar.gz")

    def storage_uri(self, *, workspace_id: int, snapshot_id: int) -> str:
        path = self.archive_path(workspace_id=workspace_id, snapshot_id=snapshot_id)
        return f"file://{path}"

    def delete_archive(self, *, workspace_id: int, snapshot_id: int) -> None:
        p = Path(self.archive_path(workspace_id=workspace_id, snapshot_id=snapshot_id))
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            pass
        # Drop empty workspace dir if possible
        try:
            parent = p.parent
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass
