"""Settings validation: snapshot storage provider vs integration/cloud posture."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.libs.common.config import Settings


def test_local_snapshot_allowed_without_cloud_posture_flags() -> None:
    s = Settings(
        database_url="postgresql+psycopg://devnest:devnest@postgres:5432/devnest_dev",
        devnest_snapshot_storage_provider="local",
        devnest_expect_external_postgres=False,
        devnest_expect_remote_gateway_clients=False,
    )
    assert (s.devnest_snapshot_storage_provider or "").strip().lower() == "local"


def test_cloud_posture_requires_s3_not_local() -> None:
    with pytest.raises(RuntimeError, match="DEVNEST_SNAPSHOT_STORAGE_PROVIDER is not 's3'"):
        Settings(
            database_url="postgresql+psycopg://u:p@rds.example.com:5432/devnest",
            devnest_expect_external_postgres=True,
            devnest_snapshot_storage_provider="local",
        )


def test_cloud_posture_s3_requires_bucket_and_region() -> None:
    with pytest.raises(RuntimeError, match="DEVNEST_S3_SNAPSHOT_BUCKET"):
        Settings(
            database_url="postgresql+psycopg://u:p@rds.example.com:5432/devnest",
            devnest_expect_remote_gateway_clients=True,
            devnest_base_domain="203-0-113-10.sslip.io",
            devnest_frontend_public_base_url="http://203-0-113-10.sslip.io:3000",
            devnest_snapshot_storage_provider="s3",
            devnest_s3_snapshot_bucket="",
            aws_region="us-east-1",
        )


def test_cloud_posture_s3_ok_with_bucket_and_region() -> None:
    s = Settings(
        database_url="postgresql+psycopg://u:p@rds.example.com:5432/devnest",
        devnest_expect_external_postgres=True,
        devnest_base_domain="203-0-113-10.sslip.io",
        devnest_frontend_public_base_url="http://203-0-113-10.sslip.io:3000",
        devnest_snapshot_storage_provider="s3",
        devnest_s3_snapshot_bucket="snap-bucket",
        devnest_s3_snapshot_prefix="pfx",
        aws_region="us-west-2",
    )
    assert s.devnest_s3_snapshot_bucket == "snap-bucket"
    assert s.aws_region == "us-west-2"


def test_snapshot_storage_log_fields_same_keys_for_local_and_s3() -> None:
    from app.services.storage import factory as storage_factory

    local_s = MagicMock()
    local_s.devnest_snapshot_storage_provider = "local"
    local_s.devnest_s3_snapshot_bucket = ""
    local_s.devnest_s3_snapshot_prefix = "devnest-snapshots"
    local_s.aws_region = ""

    s3_s = MagicMock()
    s3_s.devnest_snapshot_storage_provider = "s3"
    s3_s.devnest_s3_snapshot_bucket = "bkt"
    s3_s.devnest_s3_snapshot_prefix = "pre"
    s3_s.aws_region = "eu-west-1"

    with patch.object(storage_factory, "get_snapshot_storage_root", return_value="/tmp/snaps"):
        with patch.object(storage_factory, "get_settings", return_value=local_s):
            loc = storage_factory.snapshot_storage_log_fields()
        with patch.object(storage_factory, "get_settings", return_value=s3_s):
            s3f = storage_factory.snapshot_storage_log_fields()

    assert loc["provider"] == "local"
    assert loc["bucket"] == loc["prefix"] == loc["region"] == "-"
    assert loc["root"] == "/tmp/snaps"

    assert s3f["provider"] == "s3"
    assert s3f["bucket"] == "bkt"
    assert s3f["prefix"] == "pre"
    assert s3f["region"] == "eu-west-1"
    assert s3f["root"] == "-"
