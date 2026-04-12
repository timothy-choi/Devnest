"""Unit tests: local snapshot storage paths and delete."""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.services.storage.local_filesystem import LocalFilesystemSnapshotStorage


def test_archive_path_and_uri_stable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        s = LocalFilesystemSnapshotStorage(tmp)
        p = s.archive_path(workspace_id=42, snapshot_id=7)
        assert p.endswith("ws-42/snapshot-7.tar.gz")
        uri = s.storage_uri(workspace_id=42, snapshot_id=7)
        assert uri.startswith("file://")
        assert "ws-42" in uri and "snapshot-7.tar.gz" in uri


def test_delete_archive_removes_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        s = LocalFilesystemSnapshotStorage(tmp)
        p = Path(s.archive_path(workspace_id=1, snapshot_id=1))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
        s.delete_archive(workspace_id=1, snapshot_id=1)
        assert not p.is_file()


def test_has_nonempty_archive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        s = LocalFilesystemSnapshotStorage(tmp)
        assert s.has_nonempty_archive(workspace_id=1, snapshot_id=1) is False
        Path(s.archive_path(workspace_id=1, snapshot_id=1)).write_bytes(b"x")
        assert s.has_nonempty_archive(workspace_id=1, snapshot_id=1) is True
        Path(s.archive_path(workspace_id=1, snapshot_id=1)).write_bytes(b"")
        assert s.has_nonempty_archive(workspace_id=1, snapshot_id=1) is False
