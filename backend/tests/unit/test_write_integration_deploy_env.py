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
