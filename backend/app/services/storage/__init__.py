"""Storage abstractions for workspace snapshots (V1 local filesystem; future S3/EFS/object providers)."""

from .factory import get_snapshot_storage_provider
from .interfaces import SnapshotStorageProvider
from .local_filesystem import LocalFilesystemSnapshotStorage

__all__ = [
    "LocalFilesystemSnapshotStorage",
    "SnapshotStorageProvider",
    "get_snapshot_storage_provider",
]
