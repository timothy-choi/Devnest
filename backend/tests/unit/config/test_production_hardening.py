"""Startup guards for staging/production (placement, IDE readiness, reconcile locking)."""

from __future__ import annotations

import pytest

_GOOD_JWT = "x" * 48
_PROD_BASE = {
    "devnest_allow_runtime_env_fallback": False,
}
_PROD_KW = {
    **_PROD_BASE,
    "devnest_workspace_http_probe_enabled": True,
    "devnest_require_ide_http_probe": True,
}


class TestStagingProductionGuards:
    def test_staging_rejects_runtime_env_fallback(self) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415

        with pytest.raises(RuntimeError, match="DEVNEST_ALLOW_RUNTIME_ENV_FALLBACK"):
            Settings(
                database_url="postgresql+psycopg://u:p@localhost/db",
                jwt_secret_key=_GOOD_JWT,
                devnest_env="staging",
                devnest_allow_runtime_env_fallback=True,
            )

    def test_production_rejects_tcp_only_ide_when_require_http(self) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415

        with pytest.raises(RuntimeError, match="DEVNEST_WORKSPACE_HTTP_PROBE_ENABLED"):
            Settings(
                database_url="postgresql+psycopg://u:p@localhost/db",
                jwt_secret_key=_GOOD_JWT,
                devnest_env="production",
                **_PROD_BASE,
                devnest_workspace_http_probe_enabled=False,
                devnest_require_ide_http_probe=True,
            )

    def test_production_rejects_portable_reconcile_lock(self) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415

        with pytest.raises(RuntimeError, match="DEVNEST_RECONCILE_LOCK_BACKEND"):
            Settings(
                database_url="postgresql+psycopg://u:p@localhost/db",
                jwt_secret_key=_GOOD_JWT,
                devnest_env="production",
                devnest_reconcile_lock_backend="portable",
                devnest_require_prod_reconcile_locking=True,
                **_PROD_KW,
            )

    def test_production_rejects_sqlite_when_reconcile_lock_required(self) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415

        with pytest.raises(RuntimeError, match="PostgreSQL"):
            Settings(
                database_url="sqlite:///./x.db",
                jwt_secret_key=_GOOD_JWT,
                devnest_env="production",
                devnest_require_prod_reconcile_locking=True,
                **_PROD_KW,
            )

    def test_staging_ok_with_postgres_advisory_defaults(self) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415

        s = Settings(
            database_url="postgresql+psycopg://u:p@localhost/db",
            jwt_secret_key=_GOOD_JWT,
            devnest_env="staging",
            **_PROD_KW,
            devnest_reconcile_lock_backend="postgres_advisory",
            devnest_require_prod_reconcile_locking=True,
        )
        assert s.devnest_reconcile_lock_backend == "postgres_advisory"
