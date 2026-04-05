"""Set test DATABASE_URL before any application imports (see tests/integration/conftest.py for DB fixtures)."""

from __future__ import annotations

import os


def _pytest_database_url() -> str:
    # GitHub Actions maps the service to localhost:5432; local docker-compose.test uses 5433.
    if os.getenv("GITHUB_ACTIONS") or os.getenv("CI"):
        return "postgresql+psycopg://test:test@127.0.0.1:5432/devnest_test"
    return "postgresql+psycopg://test:test@127.0.0.1:5433/devnest_test"


os.environ["DATABASE_URL"] = _pytest_database_url()

from app.libs.common.config import get_settings  # noqa: E402
from app.libs.db.database import reset_engine  # noqa: E402

get_settings.cache_clear()
reset_engine()
