"""Set test DATABASE_URL before any app imports (see tests/integration/conftest.py for DB fixtures)."""

from __future__ import annotations

import os

_DEFAULT_TEST_DB = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://test:test@127.0.0.1:5432/devnest_test",
)

os.environ["DATABASE_URL"] = _DEFAULT_TEST_DB

# Default test profile: local placement env fallback + relaxed IDE probe (TCP-only tests OK).
os.environ.setdefault("DEVNEST_ALLOW_RUNTIME_ENV_FALLBACK", "true")
os.environ.setdefault("DEVNEST_REQUIRE_IDE_HTTP_PROBE", "false")

from app.libs.common.config import get_settings  # noqa: E402
from app.libs.db.database import reset_engine  # noqa: E402

get_settings.cache_clear()
reset_engine()


def pytest_configure(config):  # noqa: ANN001
    """Fail fast when CI requires wall-clock timeouts but pytest-timeout is missing."""
    if os.environ.get("DEVNEST_ENFORCE_TEST_TIMEOUTS", "").strip().lower() in ("1", "true", "yes", "on"):
        if not config.pluginmanager.has_plugin("pytest_timeout"):
            raise RuntimeError(
                "DEVNEST_ENFORCE_TEST_TIMEOUTS is set but pytest-timeout is not loaded. "
                "Install backend/requirements.txt (includes pytest-timeout) before running pytest."
            )
