"""Errors raised by snapshot storage providers."""

from __future__ import annotations


class SnapshotStorageError(RuntimeError):
    """Raised when a snapshot storage operation fails due to a transient or unrecoverable error.

    Distinct from a missing object (404/NoSuchKey), which results in a ``False`` return value from
    :meth:`~app.services.storage.interfaces.SnapshotStorageProvider.has_nonempty_archive`.
    Callers should treat this exception as a restore-preflight failure and mark the snapshot
    operation as FAILED rather than silently treating it as absent.
    """
