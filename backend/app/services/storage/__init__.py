"""Storage abstractions for workspace snapshots (local filesystem and S3 providers)."""

from .factory import get_snapshot_storage_provider
from .interfaces import SnapshotStorageProvider
from .local_filesystem import LocalFilesystemSnapshotStorage
from .s3_storage import S3SnapshotStorageProvider

__all__ = [
    "LocalFilesystemSnapshotStorage",
    "S3SnapshotStorageProvider",
    "SnapshotStorageProvider",
    "get_snapshot_storage_provider",
]
