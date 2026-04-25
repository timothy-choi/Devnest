"""Resolve snapshot storage provider from settings.

Supported providers (DEVNEST_SNAPSHOT_STORAGE_PROVIDER):
  - ``local``  (default) — :class:`LocalFilesystemSnapshotStorage` — single-node / dev.
  - ``s3``               — :class:`S3SnapshotStorageProvider` — multi-node / production.

S3 provider requires:
  DEVNEST_S3_SNAPSHOT_BUCKET   — S3 bucket name (required when provider=s3).
  DEVNEST_S3_SNAPSHOT_PREFIX   — Key prefix (default: devnest-snapshots).
  AWS_REGION / aws_region      — AWS region.
  AWS_ACCESS_KEY_ID / …SECRET  — Optional; falls back to instance profile / env chain.
  DEVNEST_SNAPSHOT_TEMP_DIR    — Local temp directory for staging archives (optional).
"""

from __future__ import annotations

import os
import tempfile
from typing import Union

from app.libs.common.config import get_settings

from .local_filesystem import LocalFilesystemSnapshotStorage
from .s3_storage import S3SnapshotStorageProvider

SnapshotProvider = Union[LocalFilesystemSnapshotStorage, S3SnapshotStorageProvider]


def get_snapshot_storage_root() -> str:
    s = get_settings()
    raw = (s.devnest_snapshot_storage_root or "").strip()
    if raw:
        return raw
    base = (s.workspace_projects_base or "").strip()
    if base:
        return str(os.path.join(base, "..", "devnest-snapshots"))
    return os.path.join(tempfile.gettempdir(), "devnest-snapshots")


def snapshot_storage_log_fields() -> dict[str, str]:
    """Safe startup diagnostics for snapshot storage (same keys for local and S3; no secrets)."""
    s = get_settings()
    provider_name = (s.devnest_snapshot_storage_provider or "local").strip().lower() or "local"
    fields: dict[str, str] = {
        "provider": provider_name,
        "bucket": "-",
        "prefix": "-",
        "region": "-",
        "root": "-",
    }
    if provider_name == "s3":
        fields["bucket"] = (s.devnest_s3_snapshot_bucket or "").strip() or "<missing>"
        fields["prefix"] = (s.devnest_s3_snapshot_prefix or "devnest-snapshots").strip() or "devnest-snapshots"
        fields["region"] = (s.aws_region or "").strip() or "<missing>"
        return fields

    fields["root"] = get_snapshot_storage_root()
    return fields


def get_snapshot_storage_provider() -> SnapshotProvider:
    """Return the configured snapshot storage provider.

    Selecting the provider is driven by ``DEVNEST_SNAPSHOT_STORAGE_PROVIDER``:
      - ``local`` → :class:`LocalFilesystemSnapshotStorage` (default)
      - ``s3``    → :class:`S3SnapshotStorageProvider`

    The local provider is always available and used as the dev/test default.
    """
    s = get_settings()
    provider_name = (s.devnest_snapshot_storage_provider or "local").strip().lower()

    if provider_name == "s3":
        bucket = (s.devnest_s3_snapshot_bucket or "").strip()
        region = (s.aws_region or "").strip()
        if not bucket:
            raise RuntimeError(
                "DEVNEST_S3_SNAPSHOT_BUCKET must be set when DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3"
            )
        if not region:
            raise RuntimeError("AWS_REGION must be set when DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3")
        return S3SnapshotStorageProvider(
            bucket=bucket,
            prefix=(s.devnest_s3_snapshot_prefix or "devnest-snapshots").strip(),
            region=region,
            aws_access_key_id=(s.aws_access_key_id or "").strip(),
            aws_secret_access_key=(s.aws_secret_access_key or "").strip(),
            temp_dir=(s.devnest_snapshot_temp_dir or "").strip(),
        )
    if provider_name != "local":
        raise RuntimeError(
            "DEVNEST_SNAPSHOT_STORAGE_PROVIDER must be 'local' or 's3' "
            f"(got {s.devnest_snapshot_storage_provider!r})"
        )

    # Default: local filesystem.
    return LocalFilesystemSnapshotStorage(get_snapshot_storage_root())
