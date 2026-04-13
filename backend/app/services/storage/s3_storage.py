"""S3-backed snapshot storage provider (multi-node / production).

Archives are stored under ``s3://{bucket}/{prefix}/ws-{workspace_id}/snapshot-{snapshot_id}.tar.gz``.

The orchestrator writes/reads archives on the local filesystem via :meth:`archive_path`
(a deterministic temp-local path). The worker is responsible for calling
:meth:`upload_archive` after a successful export and :meth:`download_archive` before a restore.

Credentials use the standard boto3 credential chain:
  1. Explicit ``aws_access_key_id`` / ``aws_secret_access_key`` from :class:`~app.libs.common.config.Settings`.
  2. Environment variables (``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY``).
  3. IAM instance profile (EC2) or ECS task role.
  4. ``~/.aws/credentials`` file.

TODO: Add server-side encryption (SSE-S3 or SSE-KMS) option for production hardening.
TODO: Add presigned-URL generation for direct workspace-node uploads bypassing the control plane.
TODO: Support cross-region replication configuration for multi-region durability.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import boto3 as _boto3_type

_logger = logging.getLogger(__name__)


class S3SnapshotStorageProvider:
    """
    Stores snapshot archives in S3.

    ``archive_path()`` returns a stable temp-local path used by the orchestrator for tar
    operations. The caller must invoke :meth:`upload_archive` / :meth:`download_archive`
    to synchronise the local staging file with the S3 object.
    """

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "devnest-snapshots",
        region: str = "",
        aws_access_key_id: str = "",
        aws_secret_access_key: str = "",
        temp_dir: str = "",
    ) -> None:
        if not bucket:
            raise ValueError("S3SnapshotStorageProvider: bucket must not be empty")
        self._bucket = bucket
        self._prefix = (prefix or "devnest-snapshots").rstrip("/")
        self._region = region or None
        self._key_id = aws_access_key_id or None
        self._secret = aws_secret_access_key or None
        self._temp_dir = (temp_dir or "").strip() or None

    def _s3_key(self, *, workspace_id: int, snapshot_id: int) -> str:
        return f"{self._prefix}/ws-{int(workspace_id)}/snapshot-{int(snapshot_id)}.tar.gz"

    def _staging_path(self, *, workspace_id: int, snapshot_id: int) -> Path:
        """Deterministic local temp path for staging the archive before upload / after download."""
        base = Path(self._temp_dir).expanduser().resolve() if self._temp_dir else Path(tempfile.gettempdir())
        staging_dir = base / "devnest-snapshots-staging" / f"ws-{int(workspace_id)}"
        staging_dir.mkdir(parents=True, exist_ok=True)
        return staging_dir / f"snapshot-{int(snapshot_id)}.tar.gz"

    def _client(self):  # noqa: ANN202
        """Lazy boto3 S3 client; import deferred so s3_storage module loads without boto3 at import time."""
        import boto3  # noqa: PLC0415

        kwargs: dict = {}
        if self._region:
            kwargs["region_name"] = self._region
        if self._key_id:
            kwargs["aws_access_key_id"] = self._key_id
        if self._secret:
            kwargs["aws_secret_access_key"] = self._secret
        return boto3.client("s3", **kwargs)

    def archive_path(self, *, workspace_id: int, snapshot_id: int) -> str:
        """Local staging path for the archive.

        The orchestrator writes here (export) or reads from here (import). Use
        :meth:`upload_archive` after export and :meth:`download_archive` before import.
        """
        return str(self._staging_path(workspace_id=workspace_id, snapshot_id=snapshot_id))

    def storage_uri(self, *, workspace_id: int, snapshot_id: int) -> str:
        """Opaque ``s3://`` URI persisted on ``WorkspaceSnapshot.storage_uri``."""
        key = self._s3_key(workspace_id=workspace_id, snapshot_id=snapshot_id)
        return f"s3://{self._bucket}/{key}"

    def has_nonempty_archive(self, *, workspace_id: int, snapshot_id: int) -> bool:
        """True when the S3 object exists with size > 0 (restore preflight)."""
        key = self._s3_key(workspace_id=workspace_id, snapshot_id=snapshot_id)
        try:
            resp = self._client().head_object(Bucket=self._bucket, Key=key)
            return int(resp.get("ContentLength", 0)) > 0
        except Exception as exc:
            # botocore.exceptions.ClientError 404 → object missing
            code = getattr(getattr(exc, "response", None), "get", lambda *a: None)("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey"):
                return False
            _logger.warning(
                "s3_storage.has_nonempty_archive_error",
                extra={"bucket": self._bucket, "key": key},
                exc_info=True,
            )
            return False

    def delete_archive(self, *, workspace_id: int, snapshot_id: int) -> None:
        """Delete the S3 object; no-op when missing."""
        key = self._s3_key(workspace_id=workspace_id, snapshot_id=snapshot_id)
        try:
            self._client().delete_object(Bucket=self._bucket, Key=key)
        except Exception:
            _logger.warning(
                "s3_storage.delete_archive_error",
                extra={"bucket": self._bucket, "key": key},
                exc_info=True,
            )
        # Also clean up the local staging file if present.
        try:
            p = self._staging_path(workspace_id=workspace_id, snapshot_id=snapshot_id)
            if p.is_file():
                p.unlink()
        except OSError:
            pass

    def upload_archive(self, *, workspace_id: int, snapshot_id: int) -> None:
        """Upload the local staging archive to S3 after a successful export.

        Raises :class:`RuntimeError` on upload failure (caller should treat the snapshot as FAILED).
        """
        local_path = self._staging_path(workspace_id=workspace_id, snapshot_id=snapshot_id)
        key = self._s3_key(workspace_id=workspace_id, snapshot_id=snapshot_id)
        _logger.info(
            "s3_storage.upload_started",
            extra={"bucket": self._bucket, "key": key, "local_path": str(local_path)},
        )
        try:
            self._client().upload_file(str(local_path), self._bucket, key)
        except Exception as exc:
            _logger.error(
                "s3_storage.upload_failed",
                extra={"bucket": self._bucket, "key": key},
                exc_info=True,
            )
            raise RuntimeError(f"S3 upload failed for ws={workspace_id} snap={snapshot_id}: {exc}") from exc
        _logger.info(
            "s3_storage.upload_succeeded",
            extra={"bucket": self._bucket, "key": key},
        )

    def download_archive(self, *, workspace_id: int, snapshot_id: int) -> None:
        """Download the S3 archive to the local staging path before a restore.

        Raises :class:`RuntimeError` on download failure (caller should treat the restore as FAILED).
        """
        local_path = self._staging_path(workspace_id=workspace_id, snapshot_id=snapshot_id)
        key = self._s3_key(workspace_id=workspace_id, snapshot_id=snapshot_id)
        _logger.info(
            "s3_storage.download_started",
            extra={"bucket": self._bucket, "key": key, "local_path": str(local_path)},
        )
        try:
            self._client().download_file(self._bucket, key, str(local_path))
        except Exception as exc:
            _logger.error(
                "s3_storage.download_failed",
                extra={"bucket": self._bucket, "key": key},
                exc_info=True,
            )
            raise RuntimeError(f"S3 download failed for ws={workspace_id} snap={snapshot_id}: {exc}") from exc
        _logger.info(
            "s3_storage.download_succeeded",
            extra={"bucket": self._bucket, "key": key},
        )
