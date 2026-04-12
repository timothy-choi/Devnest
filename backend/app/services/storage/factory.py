"""Resolve snapshot storage provider from settings."""

from __future__ import annotations

import os
import tempfile

from app.libs.common.config import get_settings

from .local_filesystem import LocalFilesystemSnapshotStorage


def get_snapshot_storage_root() -> str:
    s = get_settings()
    raw = (s.devnest_snapshot_storage_root or "").strip()
    if raw:
        return raw
    base = (s.workspace_projects_base or "").strip()
    if base:
        return str(os.path.join(base, "..", "devnest-snapshots"))
    return os.path.join(tempfile.gettempdir(), "devnest-snapshots")


def get_snapshot_storage_provider() -> LocalFilesystemSnapshotStorage:
    """V1: always local filesystem; swap for multi-backend registry later."""
    return LocalFilesystemSnapshotStorage(get_snapshot_storage_root())
