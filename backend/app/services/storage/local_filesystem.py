"""Local filesystem snapshot storage (single-node / dev V1)."""

from __future__ import annotations

from pathlib import Path


class LocalFilesystemSnapshotStorage:
    """
    Stores archives under ``{root}/ws-{workspace_id}/snapshot-{snapshot_id}.tar.gz``.

    Paths are resolved under ``root_dir`` so symlink tricks cannot escape the bucket.

    TODO: Add S3/EFS provider implementing :class:`SnapshotStorageProvider` for multi-node clusters.
    """

    def __init__(self, root_dir: str) -> None:
        self._root = Path(root_dir).expanduser().resolve()

    def _resolved_archive(self, *, workspace_id: int, snapshot_id: int) -> Path:
        d = self._root / f"ws-{int(workspace_id)}"
        d.mkdir(parents=True, exist_ok=True)
        p = (d / f"snapshot-{int(snapshot_id)}.tar.gz").resolve()
        root_r = self._root.resolve()
        if p != root_r and root_r not in p.parents:
            raise ValueError("snapshot archive path escaped storage root")
        return p

    def archive_path(self, *, workspace_id: int, snapshot_id: int) -> str:
        return str(self._resolved_archive(workspace_id=workspace_id, snapshot_id=snapshot_id))

    def storage_uri(self, *, workspace_id: int, snapshot_id: int) -> str:
        path = self.archive_path(workspace_id=workspace_id, snapshot_id=snapshot_id)
        return f"file://{path}"

    def has_nonempty_archive(self, *, workspace_id: int, snapshot_id: int) -> bool:
        p = self._resolved_archive(workspace_id=workspace_id, snapshot_id=snapshot_id)
        try:
            return p.is_file() and p.stat().st_size > 0
        except OSError:
            return False

    def delete_archive(self, *, workspace_id: int, snapshot_id: int) -> None:
        p = self._resolved_archive(workspace_id=workspace_id, snapshot_id=snapshot_id)
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
