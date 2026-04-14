"""Unit tests for Redis and distributed rate-limit config validation (Task 1)."""

from __future__ import annotations

import pytest


class TestRedisConfig:
    def test_default_redis_url_empty(self) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415
        s = Settings(database_url="postgresql://x:y@h/d")
        assert s.devnest_redis_url == ""

    def test_default_rate_limit_backend_is_memory(self) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415
        s = Settings(database_url="postgresql://x:y@h/d")
        assert s.devnest_rate_limit_backend == "memory"

    def test_unknown_backend_normalizes_to_memory(self) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415
        s = Settings(database_url="postgresql://x:y@h/d", devnest_rate_limit_backend="unknown_backend")
        assert s.devnest_rate_limit_backend == "memory"

    def test_redis_backend_accepted(self) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415
        s = Settings(
            database_url="postgresql://x:y@h/d",
            devnest_rate_limit_backend="redis",
            devnest_redis_url="redis://localhost:6379",
        )
        assert s.devnest_rate_limit_backend == "redis"
        assert s.devnest_redis_url == "redis://localhost:6379"

    def test_require_distributed_raises_when_url_missing(self) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415
        with pytest.raises(RuntimeError, match="DEVNEST_REDIS_URL is empty"):
            Settings(
                database_url="postgresql://x:y@h/d",
                devnest_rate_limit_backend="redis",
                devnest_redis_url="",
                devnest_require_distributed_rate_limiting=True,
            )

    def test_require_distributed_ok_when_url_set(self) -> None:
        from app.libs.common.config import Settings  # noqa: PLC0415
        s = Settings(
            database_url="postgresql://x:y@h/d",
            devnest_rate_limit_backend="redis",
            devnest_redis_url="redis://localhost:6379",
            devnest_require_distributed_rate_limiting=True,
        )
        assert s.devnest_redis_url != ""

    def test_require_distributed_no_raise_when_backend_memory(self) -> None:
        """No error when backend is memory even if require flag is set."""
        from app.libs.common.config import Settings  # noqa: PLC0415
        s = Settings(
            database_url="postgresql://x:y@h/d",
            devnest_rate_limit_backend="memory",
            devnest_redis_url="",
            devnest_require_distributed_rate_limiting=True,
        )
        assert s.devnest_rate_limit_backend == "memory"
