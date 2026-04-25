"""Tests for scripts/write_integration_deploy_env.py (deploy .env.integration writer)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "write_integration_deploy_env.py"


def _load_writer():
    spec = importlib.util.spec_from_file_location("write_integration_deploy_env", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def w():
    return _load_writer()


def test_parse_libpq_keyword_conninfo_quoted_password(w):
    d = w.parse_libpq_keyword_conninfo("password='don''t' host=h")
    assert d["password"] == "don't"
    assert d["host"] == "h"


def test_libpq_to_psycopg_url_basic(w):
    s = "host=db.example.com port=5432 dbname=app user=u password=secret sslmode=require"
    out = w._libpq_keyword_dsn_to_psycopg_url(s)
    assert out.startswith("postgresql+psycopg://")
    assert "db.example.com:5432" in out
    assert out.endswith("sslmode=require") or "?sslmode=require" in out
    assert "/app" in out
    assert "u:secret@" in out


def test_libpq_ipv6_host_brackets(w):
    s = "host=::1 port=5432 dbname=d user=u password=p"
    out = w._libpq_keyword_dsn_to_psycopg_url(s)
    assert "[::1]:5432" in out


def test_normalize_accepts_existing_psycopg_url(w):
    u = "postgresql+psycopg://u:p@h.example:5432/mydb?sslmode=require"
    assert w.normalize_database_url_for_deploy("DATABASE_URL", u) == u


def test_normalize_libpq_round_trip(w):
    libpq = "host=h.postgres.host dbname=db user=usr password=p sslmode=require"
    out = w.normalize_database_url_for_deploy("DATABASE_URL", libpq)
    w.validate_postgresql_psycopg_url("DATABASE_URL", out)
    assert out.startswith("postgresql+psycopg://usr:p@h.postgres.host:5432/db")
    assert "sslmode=require" in out


def test_normalize_rejects_plain_postgres_url(w):
    with pytest.raises(ValueError, match="postgresql\\+psycopg"):
        w.normalize_database_url_for_deploy(
            "DATABASE_URL", "postgresql://u:p@localhost:5432/db"
        )


def test_validate_parsed_external_requires_expect_flags(w):
    base = {
        "DATABASE_URL": "postgresql+psycopg://u:p@db.example.com:5432/mydb",
        "DEVNEST_COMPOSE_DATABASE_URL": "postgresql+psycopg://u:p@db.example.com:5432/mydb",
        "DEVNEST_DATABASE_URL": "postgresql+psycopg://u:p@db.example.com:5432/mydb",
        "DEVNEST_SNAPSHOT_STORAGE_PROVIDER": "s3",
        "DEVNEST_S3_SNAPSHOT_BUCKET": "b",
        "DEVNEST_S3_SNAPSHOT_PREFIX": "pfx",
        "AWS_REGION": "us-east-1",
        "DEVNEST_BASE_DOMAIN": "example.sslip.io",
        "DEVNEST_GATEWAY_PUBLIC_SCHEME": "http",
        "DEVNEST_GATEWAY_PUBLIC_PORT": "9081",
        "DEVNEST_FRONTEND_PUBLIC_BASE_URL": "http://example.sslip.io:3000",
        "NEXT_PUBLIC_APP_BASE_URL": "http://example.sslip.io:3000",
        "NEXT_PUBLIC_API_BASE_URL": "http://example.sslip.io:8000",
        "GITHUB_OAUTH_PUBLIC_BASE_URL": "http://example.sslip.io:3000",
        "GCLOUD_OAUTH_PUBLIC_BASE_URL": "http://example.sslip.io:3000",
        "OAUTH_GITHUB_CLIENT_ID": "id",
        "OAUTH_GITHUB_CLIENT_SECRET": "sec",
    }
    with pytest.raises(ValueError, match="DEVNEST_EXPECT_EXTERNAL_POSTGRES"):
        w.validate_parsed(base)
    base["DEVNEST_EXPECT_EXTERNAL_POSTGRES"] = "true"
    with pytest.raises(ValueError, match="DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS"):
        w.validate_parsed(base)
    base["DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS"] = "true"
    w.validate_parsed(base)


def test_print_integration_deploy_diagnostics_smoke(capsys, w):
    w.print_integration_deploy_diagnostics(
        {
            "DATABASE_URL": "postgresql+psycopg://u:p@db.example.com:5432/mydb",
            "DEVNEST_SNAPSHOT_STORAGE_PROVIDER": "s3",
            "DEVNEST_S3_SNAPSHOT_BUCKET": "my-bucket",
            "DEVNEST_S3_SNAPSHOT_PREFIX": "pfx",
            "AWS_REGION": "us-east-1",
            "DEVNEST_FRONTEND_PUBLIC_BASE_URL": "http://example.sslip.io:3000",
            "NEXT_PUBLIC_API_BASE_URL": "http://example.sslip.io:8000",
            "NEXT_PUBLIC_APP_BASE_URL": "http://example.sslip.io:3000",
            "GITHUB_OAUTH_PUBLIC_BASE_URL": "http://example.sslip.io:3000",
            "GCLOUD_OAUTH_PUBLIC_BASE_URL": "http://example.sslip.io:3000",
            "OAUTH_GITHUB_CLIENT_ID": "id",
            "OAUTH_GITHUB_CLIENT_SECRET": "sec",
            "OAUTH_GOOGLE_CLIENT_ID": "",
            "OAUTH_GOOGLE_CLIENT_SECRET": "",
            "DEVNEST_BASE_DOMAIN": "example.sslip.io",
            "DEVNEST_GATEWAY_PUBLIC_SCHEME": "http",
            "DEVNEST_GATEWAY_PUBLIC_PORT": "9081",
            "DEVNEST_EXPECT_EXTERNAL_POSTGRES": "true",
            "DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS": "true",
        }
    )
    out = capsys.readouterr().out
    assert "database_host=db.example.com" in out
    assert "database_name=mydb" in out
    assert "snapshot_provider=s3" in out
    assert "s3_bucket=set" in out
    assert "oauth_github_configured=True" in out
    assert "oauth_google_configured=False" in out
    assert "devnest_base_domain=example.sslip.io" in out
    assert "expect_external_postgres=True" in out
    assert "expect_remote_gateway_clients=True" in out
