"""Tests for in-process rate limiting (Task 5)."""

from __future__ import annotations

import time
import threading
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.libs.security.rate_limit import (
    SlidingWindowRateLimiter,
    RateLimitMiddleware,
    make_rate_limit_dependency,
)


# ── SlidingWindowRateLimiter unit tests ───────────────────────────────────────


class TestSlidingWindowRateLimiter:

    def test_allows_requests_under_limit(self) -> None:
        limiter = SlidingWindowRateLimiter(calls=5, period=60)
        for _ in range(5):
            assert limiter.is_allowed("user1") is True

    def test_blocks_when_limit_exceeded(self) -> None:
        limiter = SlidingWindowRateLimiter(calls=3, period=60)
        for _ in range(3):
            limiter.is_allowed("user1")
        assert limiter.is_allowed("user1") is False

    def test_different_keys_are_independent(self) -> None:
        limiter = SlidingWindowRateLimiter(calls=1, period=60)
        assert limiter.is_allowed("a") is True
        assert limiter.is_allowed("b") is True  # Different key — not blocked.
        assert limiter.is_allowed("a") is False  # Same key — blocked.

    def test_window_expires_and_allows_new_requests(self) -> None:
        limiter = SlidingWindowRateLimiter(calls=1, period=1)  # 1-second window
        assert limiter.is_allowed("x") is True
        assert limiter.is_allowed("x") is False  # Within window.
        time.sleep(1.1)
        assert limiter.is_allowed("x") is True  # Window expired.

    def test_retry_after_returns_positive_when_blocked(self) -> None:
        limiter = SlidingWindowRateLimiter(calls=1, period=60)
        limiter.is_allowed("k")
        limiter.is_allowed("k")  # Blocked.
        retry = limiter.retry_after_seconds("k")
        assert retry > 0

    def test_retry_after_returns_zero_when_not_blocked(self) -> None:
        limiter = SlidingWindowRateLimiter(calls=5, period=60)
        retry = limiter.retry_after_seconds("new_key")
        assert retry == 0.0

    def test_constructor_raises_on_invalid_args(self) -> None:
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(calls=0, period=60)
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(calls=5, period=0)

    def test_thread_safe_concurrent_access(self) -> None:
        limiter = SlidingWindowRateLimiter(calls=100, period=60)
        results = []
        lock = threading.Lock()

        def _worker():
            for _ in range(10):
                r = limiter.is_allowed("concurrent")
                with lock:
                    results.append(r)

        threads = [threading.Thread(target=_worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        allowed = sum(1 for r in results if r is True)
        blocked = sum(1 for r in results if r is False)
        assert allowed == 100  # Exactly 100 allowed.
        assert blocked == 100  # 200 total - 100 = 100 blocked.


# ── RateLimitMiddleware integration tests ────────────────────────────────────


def _make_app(calls: int = 5) -> FastAPI:
    """Test FastAPI app with RateLimitMiddleware and a dummy settings mock."""
    test_app = FastAPI()
    test_app.add_middleware(RateLimitMiddleware, default_calls=calls, default_period=60)

    @test_app.get("/ping")
    def ping():
        return {"ok": True}

    return test_app


def _mock_settings(enabled: bool = True) -> MagicMock:
    s = MagicMock()
    s.devnest_rate_limit_enabled = enabled
    return s


class TestRateLimitMiddleware:

    def test_allows_requests_under_limit(self) -> None:
        app = _make_app(calls=5)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("app.libs.common.config.get_settings", return_value=_mock_settings()):
            for _ in range(5):
                resp = client.get("/ping")
                assert resp.status_code == 200

    def test_blocks_when_limit_exceeded(self) -> None:
        app = _make_app(calls=2)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("app.libs.common.config.get_settings", return_value=_mock_settings()):
            client.get("/ping")
            client.get("/ping")
            resp = client.get("/ping")
        assert resp.status_code == 429

    def test_disabled_allows_unlimited_requests(self) -> None:
        app = _make_app(calls=1)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("app.libs.common.config.get_settings", return_value=_mock_settings(enabled=False)):
            for _ in range(10):
                resp = client.get("/ping")
                assert resp.status_code == 200

    def test_429_includes_retry_after_header(self) -> None:
        app = _make_app(calls=1)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("app.libs.common.config.get_settings", return_value=_mock_settings()):
            client.get("/ping")
            resp = client.get("/ping")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        assert int(resp.headers["Retry-After"]) >= 1


# ── make_rate_limit_dependency tests ─────────────────────────────────────────


class TestMakeRateLimitDependency:

    def test_dependency_allows_requests_under_limit(self) -> None:
        dep = make_rate_limit_dependency(calls=3, period=60)
        test_app = FastAPI()
        from fastapi import Depends

        @test_app.get("/test", dependencies=[Depends(dep)])
        def test_route():
            return {"ok": True}

        client = TestClient(test_app, raise_server_exceptions=False)
        with patch("app.libs.common.config.get_settings", return_value=_mock_settings()):
            for _ in range(3):
                resp = client.get("/test")
                assert resp.status_code == 200

    def test_dependency_blocks_when_limit_exceeded(self) -> None:
        dep = make_rate_limit_dependency(calls=2, period=60)
        test_app = FastAPI()
        from fastapi import Depends

        @test_app.get("/limited", dependencies=[Depends(dep)])
        def limited():
            return {"ok": True}

        client = TestClient(test_app, raise_server_exceptions=False)
        with patch("app.libs.common.config.get_settings", return_value=_mock_settings()):
            client.get("/limited")
            client.get("/limited")
            resp = client.get("/limited")
        assert resp.status_code == 429
