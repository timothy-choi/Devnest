"""Set test DATABASE_URL before any app imports (see tests/integration/conftest.py for DB fixtures)."""

from __future__ import annotations

import os

import pytest

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

# In GitHub Actions, require pytest-timeout to be loaded so misconfigured jobs fail fast.
if os.environ.get("GITHUB_ACTIONS", "").strip().lower() in ("1", "true", "yes") and not (
    os.environ.get("DEVNEST_ENFORCE_TEST_TIMEOUTS", "").strip()
):
    os.environ["DEVNEST_ENFORCE_TEST_TIMEOUTS"] = "1"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Apply a shorter default timeout to unit tests (unless overridden by @pytest.mark.timeout)."""
    if getattr(config.option, "collectonly", False):
        return
    for item in items:
        if item.get_closest_marker("timeout"):
            continue
        try:
            path_s = str(item.path)
        except Exception:
            path_s = str(getattr(item, "fspath", ""))
        norm = path_s.replace("\\", "/")
        if "/tests/unit/" in norm:
            item.add_marker(pytest.mark.timeout(120, method="thread"))


def _pytest_timeout_active(config) -> bool:  # noqa: ANN001
    """pytest-timeout registers as plugin name ``timeout`` (see ``pytest --trace-config``)."""
    pm = config.pluginmanager
    return bool(pm.has_plugin("timeout") or pm.has_plugin("pytest_timeout"))


def pytest_configure(config):  # noqa: ANN001
    """Fail fast when CI requires wall-clock timeouts but pytest-timeout is missing."""
    if os.environ.get("DEVNEST_ENFORCE_TEST_TIMEOUTS", "").strip().lower() in ("1", "true", "yes", "on"):
        if not _pytest_timeout_active(config):
            raise RuntimeError(
                "DEVNEST_ENFORCE_TEST_TIMEOUTS is set but pytest-timeout is not loaded. "
                "Install backend/requirements.txt (includes pytest-timeout) before running pytest."
            )
