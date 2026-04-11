"""Set test DATABASE_URL before any app imports (see tests/integration/conftest.py for DB fixtures)."""

from __future__ import annotations

import os

_DEFAULT_TEST_DB = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://test:test@127.0.0.1:5432/devnest_test",
)

os.environ["DATABASE_URL"] = _DEFAULT_TEST_DB

from app.libs.common.config import get_settings  # noqa: E402
from app.libs.db.database import reset_engine  # noqa: E402

get_settings.cache_clear()
reset_engine()
