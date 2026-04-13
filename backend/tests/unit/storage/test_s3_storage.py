"""Unit tests: S3SnapshotStorageProvider using moto mock AWS.

Tests cover:
  - storage_uri format
  - archive_path returns consistent staging path
  - upload_archive / download_archive round-trip
  - has_nonempty_archive reflects S3 object presence
  - delete_archive removes S3 object
  - has_nonempty_archive returns False for missing object (no exception)
  - upload failure raises RuntimeError
  - download failure raises RuntimeError
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# moto is a dev/test dependency; skip tests gracefully if not installed.
boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from moto import mock_aws  # noqa: E402

from app.services.storage.s3_storage import S3SnapshotStorageProvider  # noqa: E402

BUCKET = "devnest-test-snapshots"
PREFIX = "devnest-snapshots"
REGION = "us-east-1"
WS_ID = 10
SNAP_ID = 3


def _provider(tmp_dir: str) -> S3SnapshotStorageProvider:
    return S3SnapshotStorageProvider(
        bucket=BUCKET,
        prefix=PREFIX,
        region=REGION,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        temp_dir=tmp_dir,
    )


def _create_bucket(s3_client) -> None:
    s3_client.create_bucket(Bucket=BUCKET)


# ---------------------------------------------------------------------------
# storage_uri / archive_path
# ---------------------------------------------------------------------------


def test_storage_uri_format():
    with tempfile.TemporaryDirectory() as tmp:
        p = _provider(tmp)
        uri = p.storage_uri(workspace_id=WS_ID, snapshot_id=SNAP_ID)
        assert uri == f"s3://{BUCKET}/{PREFIX}/ws-{WS_ID}/snapshot-{SNAP_ID}.tar.gz"


def test_archive_path_returns_local_staging_path():
    with tempfile.TemporaryDirectory() as tmp:
        p = _provider(tmp)
        path = p.archive_path(workspace_id=WS_ID, snapshot_id=SNAP_ID)
        assert path.endswith(f"snapshot-{SNAP_ID}.tar.gz")
        assert f"ws-{WS_ID}" in path
        # archive_path should be under the temp_dir
        assert path.startswith(tmp)


def test_bucket_required():
    with pytest.raises(ValueError, match="bucket"):
        S3SnapshotStorageProvider(bucket="")


# ---------------------------------------------------------------------------
# upload_archive / download_archive round-trip
# ---------------------------------------------------------------------------


@mock_aws
def test_upload_and_download_round_trip():
    import boto3 as _boto3

    with tempfile.TemporaryDirectory() as tmp:
        p = _provider(tmp)
        _create_bucket(_boto3.client("s3", region_name=REGION))

        # Write a fake archive to the staging path.
        staging = Path(p.archive_path(workspace_id=WS_ID, snapshot_id=SNAP_ID))
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_bytes(b"fake-tar-content")

        p.upload_archive(workspace_id=WS_ID, snapshot_id=SNAP_ID)

        # Remove the local file to ensure download actually fetches from S3.
        staging.unlink()
        assert not staging.exists()

        p.download_archive(workspace_id=WS_ID, snapshot_id=SNAP_ID)
        assert staging.exists()
        assert staging.read_bytes() == b"fake-tar-content"


# ---------------------------------------------------------------------------
# has_nonempty_archive
# ---------------------------------------------------------------------------


@mock_aws
def test_has_nonempty_archive_false_when_missing():
    import boto3 as _boto3

    with tempfile.TemporaryDirectory() as tmp:
        p = _provider(tmp)
        _create_bucket(_boto3.client("s3", region_name=REGION))
        assert p.has_nonempty_archive(workspace_id=WS_ID, snapshot_id=SNAP_ID) is False


@mock_aws
def test_has_nonempty_archive_true_after_upload():
    import boto3 as _boto3

    with tempfile.TemporaryDirectory() as tmp:
        p = _provider(tmp)
        _create_bucket(_boto3.client("s3", region_name=REGION))

        staging = Path(p.archive_path(workspace_id=WS_ID, snapshot_id=SNAP_ID))
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_bytes(b"data")
        p.upload_archive(workspace_id=WS_ID, snapshot_id=SNAP_ID)

        assert p.has_nonempty_archive(workspace_id=WS_ID, snapshot_id=SNAP_ID) is True


# ---------------------------------------------------------------------------
# delete_archive
# ---------------------------------------------------------------------------


@mock_aws
def test_delete_archive_removes_s3_object():
    import boto3 as _boto3

    with tempfile.TemporaryDirectory() as tmp:
        p = _provider(tmp)
        s3 = _boto3.client("s3", region_name=REGION)
        _create_bucket(s3)

        staging = Path(p.archive_path(workspace_id=WS_ID, snapshot_id=SNAP_ID))
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_bytes(b"data")
        p.upload_archive(workspace_id=WS_ID, snapshot_id=SNAP_ID)

        assert p.has_nonempty_archive(workspace_id=WS_ID, snapshot_id=SNAP_ID) is True
        p.delete_archive(workspace_id=WS_ID, snapshot_id=SNAP_ID)
        assert p.has_nonempty_archive(workspace_id=WS_ID, snapshot_id=SNAP_ID) is False


@mock_aws
def test_delete_archive_no_op_when_missing():
    import boto3 as _boto3

    with tempfile.TemporaryDirectory() as tmp:
        p = _provider(tmp)
        _create_bucket(_boto3.client("s3", region_name=REGION))
        # Should not raise.
        p.delete_archive(workspace_id=99, snapshot_id=99)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_upload_failure_raises_runtime_error():
    with tempfile.TemporaryDirectory() as tmp:
        p = _provider(tmp)
        staging = Path(p.archive_path(workspace_id=WS_ID, snapshot_id=SNAP_ID))
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_bytes(b"x")

        error_client = MagicMock()
        error_client.upload_file.side_effect = Exception("network error")

        with patch.object(p, "_client", return_value=error_client):
            with pytest.raises(RuntimeError, match="S3 upload failed"):
                p.upload_archive(workspace_id=WS_ID, snapshot_id=SNAP_ID)


def test_download_failure_raises_runtime_error():
    with tempfile.TemporaryDirectory() as tmp:
        p = _provider(tmp)

        error_client = MagicMock()
        error_client.download_file.side_effect = Exception("network error")

        with patch.object(p, "_client", return_value=error_client):
            with pytest.raises(RuntimeError, match="S3 download failed"):
                p.download_archive(workspace_id=WS_ID, snapshot_id=SNAP_ID)


# ---------------------------------------------------------------------------
# Factory integration
# ---------------------------------------------------------------------------


def test_factory_returns_local_by_default(monkeypatch):
    """Factory defaults to local provider when DEVNEST_SNAPSHOT_STORAGE_PROVIDER is unset."""
    from unittest.mock import MagicMock

    from app.services.storage.factory import get_snapshot_storage_provider
    from app.services.storage.local_filesystem import LocalFilesystemSnapshotStorage

    mock_settings = MagicMock()
    mock_settings.devnest_snapshot_storage_provider = "local"
    mock_settings.devnest_snapshot_storage_root = ""
    mock_settings.workspace_projects_base = ""

    with patch("app.services.storage.factory.get_settings", return_value=mock_settings):
        provider = get_snapshot_storage_provider()
        assert isinstance(provider, LocalFilesystemSnapshotStorage)


def test_factory_returns_s3_when_configured(monkeypatch):
    from unittest.mock import MagicMock

    from app.services.storage.factory import get_snapshot_storage_provider
    from app.services.storage.s3_storage import S3SnapshotStorageProvider

    mock_settings = MagicMock()
    mock_settings.devnest_snapshot_storage_provider = "s3"
    mock_settings.devnest_s3_snapshot_bucket = "my-bucket"
    mock_settings.devnest_s3_snapshot_prefix = "snapshots"
    mock_settings.aws_region = "us-east-1"
    mock_settings.aws_access_key_id = ""
    mock_settings.aws_secret_access_key = ""
    mock_settings.devnest_snapshot_temp_dir = ""

    with patch("app.services.storage.factory.get_settings", return_value=mock_settings):
        provider = get_snapshot_storage_provider()
        assert isinstance(provider, S3SnapshotStorageProvider)


def test_factory_s3_missing_bucket_raises(monkeypatch):
    from unittest.mock import MagicMock

    from app.services.storage.factory import get_snapshot_storage_provider

    mock_settings = MagicMock()
    mock_settings.devnest_snapshot_storage_provider = "s3"
    mock_settings.devnest_s3_snapshot_bucket = ""

    with patch("app.services.storage.factory.get_settings", return_value=mock_settings):
        with pytest.raises(RuntimeError, match="DEVNEST_S3_SNAPSHOT_BUCKET"):
            get_snapshot_storage_provider()
