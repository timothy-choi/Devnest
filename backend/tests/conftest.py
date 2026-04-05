"""Set test DATABASE_URL before any app imports (see tests/integration/conftest.py for DB fixtures)."""

from __future__ import annotations

import os

# Local: docker-compose.test.yml exposes Postgres on 5433 (see backend/.env.test).
# CI: .github/workflows/tests.yml sets DATABASE_URL before pytest — setdefault leaves it unchanged.
_DEFAULT_TEST_DB = "postgresql+psycopg://test:test@127.0.0.1:5433/devnest_test"

os.environ.setdefault("DATABASE_URL", _DEFAULT_TEST_DB)

from app.libs.common.config import get_settings  # noqa: E402
from app.libs.db.database import reset_engine  # noqa: E402

get_settings.cache_clear()
reset_engine()
