"""Database config derivation tests for external Postgres / RDS readiness."""

from __future__ import annotations

import pytest


class TestDatabaseConfig:
    def test_db_auto_create_defaults_on(self, monkeypatch) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DEVNEST_DATABASE_URL", raising=False)
        monkeypatch.setattr(Settings, "_repo_env_fallbacks", staticmethod(lambda: {}))
        s = Settings(database_url="postgresql+psycopg://u:p@db.example.com:5432/devnest")
        assert s.devnest_db_auto_create is True

    def test_non_development_also_defaults_db_auto_create_on(self, monkeypatch) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DEVNEST_DATABASE_URL", raising=False)
        monkeypatch.setattr(Settings, "_repo_env_fallbacks", staticmethod(lambda: {}))
        s = Settings(
            database_url="postgresql+psycopg://u:p@db.example.com:5432/devnest",
            devnest_env="staging",
            jwt_secret_key="x" * 48,
            devnest_allow_runtime_env_fallback=False,
            devnest_require_ide_http_probe=True,
            devnest_workspace_http_probe_enabled=True,
        )
        assert s.devnest_db_auto_create is True

    def test_explicit_database_url_wins(self, monkeypatch) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DEVNEST_DATABASE_URL", raising=False)
        monkeypatch.setattr(Settings, "_repo_env_fallbacks", staticmethod(lambda: {}))
        s = Settings(
            database_url="postgresql+psycopg://u:p@db.example.com:5432/devnest?sslmode=require",
        )
        assert s.database_url == "postgresql+psycopg://u:p@db.example.com:5432/devnest?sslmode=require"

    def test_devnest_database_url_alias_overrides_database_url(self, monkeypatch) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415

        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://local:local@localhost:5432/local_db")
        monkeypatch.setenv(
            "DEVNEST_DATABASE_URL",
            "postgresql+psycopg://devnest:secret@db.example.com:5432/devnest_db",
        )
        monkeypatch.setattr(Settings, "_repo_env_fallbacks", staticmethod(lambda: {}))
        s = Settings()
        assert s.database_url == "postgresql+psycopg://devnest:secret@db.example.com:5432/devnest_db"

    def test_libpq_style_database_url_is_supported(self, monkeypatch) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DEVNEST_DATABASE_URL", raising=False)
        monkeypatch.setattr(Settings, "_repo_env_fallbacks", staticmethod(lambda: {}))
        s = Settings(
            database_url=(
                "host=devnest-db.cjwsmsiaycvs.us-east-1.rds.amazonaws.com "
                "port=5432 dbname=devnest_db user=devnest_user"
            ),
        )
        assert s.database_url == (
            "postgresql+psycopg://devnest_user@"
            "devnest-db.cjwsmsiaycvs.us-east-1.rds.amazonaws.com:5432/devnest_db"
        )

    def test_libpq_style_database_url_preserves_password_and_ssl(self, monkeypatch) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DEVNEST_DATABASE_URL", raising=False)
        monkeypatch.setattr(Settings, "_repo_env_fallbacks", staticmethod(lambda: {}))
        s = Settings(
            database_url=(
                "host=db.example.com port=5432 dbname=devnest user=devnest_user "
                "password='p@ss word' sslmode=require sslrootcert=/etc/ssl/certs/rds-ca.pem"
            ),
        )
        assert s.database_url == (
            "postgresql+psycopg://devnest_user:p%40ss+word@db.example.com:5432/devnest"
            "?sslmode=require&sslrootcert=%2Fetc%2Fssl%2Fcerts%2Frds-ca.pem"
        )

    def test_component_fields_build_postgres_url(self, monkeypatch) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DEVNEST_DATABASE_URL", raising=False)
        monkeypatch.setattr(Settings, "_repo_env_fallbacks", staticmethod(lambda: {}))
        s = Settings(
            database_url="",
            postgres_host="db.example.com",
            postgres_port=5432,
            postgres_db="devnest",
            postgres_user="devnest",
            postgres_password="p@ss word",
            postgres_sslmode="require",
        )
        assert s.database_url == (
            "postgresql+psycopg://devnest:p%40ss+word@db.example.com:5432/devnest?sslmode=require"
        )

    def test_component_fields_include_sslrootcert_when_set(self, monkeypatch) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DEVNEST_DATABASE_URL", raising=False)
        monkeypatch.setattr(Settings, "_repo_env_fallbacks", staticmethod(lambda: {}))
        s = Settings(
            database_url="",
            postgres_host="db.example.com",
            postgres_db="devnest",
            postgres_user="devnest",
            postgres_password="secret",
            postgres_sslmode="verify-full",
            postgres_sslrootcert="/etc/ssl/certs/rds-ca.pem",
        )
        assert "sslmode=verify-full" in s.database_url
        assert "sslrootcert=%2Fetc%2Fssl%2Fcerts%2Frds-ca.pem" in s.database_url

    def test_expect_external_postgres_rejects_compose_service_hostname(self, monkeypatch) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DEVNEST_DATABASE_URL", raising=False)
        monkeypatch.setattr(Settings, "_repo_env_fallbacks", staticmethod(lambda: {}))
        with pytest.raises(RuntimeError, match="postgres"):
            Settings(
                database_url="postgresql+psycopg://u:p@postgres:5432/devnest_dev",
                devnest_expect_external_postgres=True,
            )

    def test_expect_external_postgres_allows_rds_style_host(self, monkeypatch) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DEVNEST_DATABASE_URL", raising=False)
        monkeypatch.setattr(Settings, "_repo_env_fallbacks", staticmethod(lambda: {}))
        s = Settings(
            database_url="postgresql+psycopg://u:p@db.xyz.us-east-1.rds.amazonaws.com:5432/devnest",
            devnest_expect_external_postgres=True,
        )
        assert "rds.amazonaws.com" in s.database_url

    def test_expect_remote_gateway_rejects_app_lvh_me(self, monkeypatch) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DEVNEST_DATABASE_URL", raising=False)
        monkeypatch.setattr(Settings, "_repo_env_fallbacks", staticmethod(lambda: {}))
        with pytest.raises(RuntimeError, match="app.lvh.me"):
            Settings(
                database_url="postgresql+psycopg://u:p@db.example.com:5432/db",
                devnest_base_domain="app.lvh.me",
                devnest_expect_remote_gateway_clients=True,
            )
