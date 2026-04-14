"""Unit tests for the Redis-backed rate limiter (Task 1: distributed rate limiting)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.libs.security.rate_limit import RedisRateLimiter, SlidingWindowRateLimiter, _is_redis_backend


class TestRedisRateLimiter:
    def test_allows_when_redis_unavailable_fail_open(self) -> None:
        """Fail-open: when Redis is unavailable, requests are allowed."""
        limiter = RedisRateLimiter(calls=5, period=60, redis_url="redis://nonexistent:6399")
        # _get_client will fail; is_allowed should still return True
        result = limiter.is_allowed("client-1")
        assert result is True

    def test_rejects_invalid_calls_period(self) -> None:
        with pytest.raises(ValueError):
            RedisRateLimiter(calls=0, period=60, redis_url="redis://localhost")
        with pytest.raises(ValueError):
            RedisRateLimiter(calls=5, period=-1, redis_url="redis://localhost")

    def test_retry_after_returns_zero_on_error(self) -> None:
        limiter = RedisRateLimiter(calls=5, period=60, redis_url="redis://nonexistent:6399")
        result = limiter.retry_after_seconds("client-1")
        assert result == 0.0

    def test_is_allowed_with_mock_redis_within_limit(self) -> None:
        """is_allowed returns True when count < calls."""
        limiter = RedisRateLimiter(calls=10, period=60, redis_url="redis://localhost")
        mock_client = MagicMock()
        # Pipeline result: [zremrangebyscore_result, zcard=3, zadd_result, expire_result]
        mock_client.pipeline.return_value.execute.return_value = [0, 3, 1, 1]
        limiter._client = mock_client
        assert limiter.is_allowed("ip-1") is True

    def test_is_allowed_with_mock_redis_at_limit(self) -> None:
        """is_allowed returns False when count >= calls."""
        limiter = RedisRateLimiter(calls=10, period=60, redis_url="redis://localhost")
        mock_client = MagicMock()
        # zcard = 10 (already at limit)
        mock_client.pipeline.return_value.execute.return_value = [0, 10, 1, 1]
        limiter._client = mock_client
        assert limiter.is_allowed("ip-1") is False

    def test_redis_error_resets_client_and_fails_open(self) -> None:
        """When Redis op raises, client is reset and request is allowed (fail-open)."""
        limiter = RedisRateLimiter(calls=5, period=60, redis_url="redis://localhost")
        mock_client = MagicMock()
        mock_client.pipeline.return_value.execute.side_effect = Exception("connection error")
        limiter._client = mock_client
        result = limiter.is_allowed("ip-fail")
        assert result is True
        assert limiter._client is None  # reset for reconnection


class TestIsRedisBackend:
    def test_returns_false_by_default(self) -> None:
        """Default config uses memory backend."""
        with patch("app.libs.common.config.get_settings") as mock_settings:
            mock_settings.return_value.devnest_rate_limit_backend = "memory"
            mock_settings.return_value.devnest_redis_url = ""
            assert _is_redis_backend() is False

    def test_returns_false_when_backend_redis_but_no_url(self) -> None:
        with patch("app.libs.common.config.get_settings") as mock_settings:
            mock_settings.return_value.devnest_rate_limit_backend = "redis"
            mock_settings.return_value.devnest_redis_url = ""
            assert _is_redis_backend() is False

    def test_returns_true_when_backend_redis_with_url(self) -> None:
        with patch("app.libs.common.config.get_settings") as mock_settings:
            mock_settings.return_value.devnest_rate_limit_backend = "redis"
            mock_settings.return_value.devnest_redis_url = "redis://localhost:6379"
            assert _is_redis_backend() is True

    def test_returns_false_on_settings_error(self) -> None:
        with patch("app.libs.common.config.get_settings", side_effect=Exception("no settings")):
            assert _is_redis_backend() is False


class TestSlidingWindowRateLimiterStillWorks:
    def test_allows_within_limit(self) -> None:
        limiter = SlidingWindowRateLimiter(calls=3, period=60)
        assert limiter.is_allowed("k") is True
        assert limiter.is_allowed("k") is True
        assert limiter.is_allowed("k") is True

    def test_rejects_beyond_limit(self) -> None:
        limiter = SlidingWindowRateLimiter(calls=2, period=60)
        limiter.is_allowed("k")
        limiter.is_allowed("k")
        assert limiter.is_allowed("k") is False

    def test_different_keys_independent(self) -> None:
        limiter = SlidingWindowRateLimiter(calls=1, period=60)
        assert limiter.is_allowed("a") is True
        assert limiter.is_allowed("b") is True
        assert limiter.is_allowed("a") is False
