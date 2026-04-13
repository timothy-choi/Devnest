"""In-process HTTP rate limiting (no external dependency required).

Implements a **sliding-window** per-key counter backed by a thread-safe in-memory
dict. Suitable for single-process deployments. In multi-process deployments (e.g.
multiple gunicorn workers) each process maintains its own window — effective rate
limit per-client is ``rate_limit × worker_count``; for production deployments with
many workers, move to Redis-backed limiting.

Usage
-----
1. Middleware (global, all routes):

    app.add_middleware(
        RateLimitMiddleware,
        default_calls=300,
        default_period=60,
    )

2. FastAPI dependency (targeted routes):

    from app.libs.security.rate_limit import make_rate_limit_dependency

    auth_limit = make_rate_limit_dependency(calls=20, period=60)

    @router.post("/login", dependencies=[Depends(auth_limit)])
    def login(...): ...
"""

from __future__ import annotations

import time
import threading
import logging
from collections import defaultdict

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

_logger = logging.getLogger(__name__)


class SlidingWindowRateLimiter:
    """Thread-safe, in-memory sliding window rate limiter.

    Args:
        calls:  Maximum requests allowed within ``period`` seconds.
        period: Window length in seconds.
    """

    def __init__(self, calls: int, period: int) -> None:
        if calls <= 0 or period <= 0:
            raise ValueError("calls and period must be positive integers")
        self._calls = calls
        self._period = float(period)
        # key → sorted list of monotonic timestamps within the current window
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        """Return True if the request for ``key`` is within limits, and record it."""
        now = time.monotonic()
        cutoff = now - self._period
        with self._lock:
            window = self._windows[key]
            # Evict timestamps outside the current window.
            while window and window[0] <= cutoff:
                window.pop(0)
            if len(window) >= self._calls:
                return False
            window.append(now)
            return True

    def retry_after_seconds(self, key: str) -> float:
        """Approximate seconds until the next slot is available for ``key``."""
        with self._lock:
            window = self._windows.get(key)
            if not window:
                return 0.0
            oldest = window[0]
            return max(0.0, oldest + self._period - time.monotonic())


def _client_ip(request: Request) -> str:
    """Extract client IP from request, respecting X-Forwarded-For if present."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host or "unknown"
    return "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that applies a configurable default rate limit to all routes.

    Controlled by settings:
        devnest_rate_limit_enabled  — skip entirely when False
        (per-route overrides via make_rate_limit_dependency take precedence)

    This middleware enforces a ``default_calls / default_period`` window. Apply tighter
    limits to specific endpoints using :func:`make_rate_limit_dependency`.
    """

    def __init__(
        self,
        app,
        *,
        default_calls: int = 300,
        default_period: int = 60,
    ) -> None:
        super().__init__(app)
        self._limiter = SlidingWindowRateLimiter(calls=default_calls, period=default_period)

    async def dispatch(self, request: Request, call_next) -> Response:
        from app.libs.common.config import get_settings  # noqa: PLC0415
        settings = get_settings()
        if not getattr(settings, "devnest_rate_limit_enabled", True):
            return await call_next(request)

        key = _client_ip(request)
        if not self._limiter.is_allowed(key):
            retry_after = self._limiter.retry_after_seconds(key)
            _logger.warning(
                "rate_limit_exceeded_middleware",
                extra={"ip": key, "path": request.url.path, "retry_after": retry_after},
            )
            return Response(
                content='{"detail":"rate_limit_exceeded"}',
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                media_type="application/json",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
        return await call_next(request)


def make_rate_limit_dependency(
    calls: int,
    period: int = 60,
):
    """Factory: returns a FastAPI dependency that enforces a per-IP rate limit.

    Usage::

        @router.post("/login", dependencies=[Depends(make_rate_limit_dependency(calls=20))])
        def login(...): ...
    """
    limiter = SlidingWindowRateLimiter(calls=calls, period=period)

    def _check(request: Request) -> None:
        from app.libs.common.config import get_settings  # noqa: PLC0415
        if not getattr(get_settings(), "devnest_rate_limit_enabled", True):
            return
        key = _client_ip(request)
        if not limiter.is_allowed(key):
            retry_after = limiter.retry_after_seconds(key)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate_limit_exceeded",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

    return _check


# Pre-built limiters for common use cases.
# Import these in route files to add per-endpoint limits.

def auth_rate_limit(request: Request) -> None:
    """20 req/min per IP — apply to login, register, forgot-password endpoints."""
    from app.libs.common.config import get_settings  # noqa: PLC0415
    settings = get_settings()
    if not getattr(settings, "devnest_rate_limit_enabled", True):
        return
    calls = int(getattr(settings, "devnest_rate_limit_auth_per_minute", 20))
    _auth_limiter_instance = _get_or_create_limiter("auth", calls=calls, period=60)
    key = _client_ip(request)
    if not _auth_limiter_instance.is_allowed(key):
        retry = _auth_limiter_instance.retry_after_seconds(key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="auth_rate_limit_exceeded",
            headers={"Retry-After": str(int(retry) + 1)},
        )


def sse_rate_limit(request: Request) -> None:
    """30 req/min per IP — apply to the SSE /events endpoint."""
    from app.libs.common.config import get_settings  # noqa: PLC0415
    settings = get_settings()
    if not getattr(settings, "devnest_rate_limit_enabled", True):
        return
    calls = int(getattr(settings, "devnest_rate_limit_sse_per_minute", 30))
    _sse_limiter_instance = _get_or_create_limiter("sse", calls=calls, period=60)
    key = _client_ip(request)
    if not _sse_limiter_instance.is_allowed(key):
        retry = _sse_limiter_instance.retry_after_seconds(key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="sse_rate_limit_exceeded",
            headers={"Retry-After": str(int(retry) + 1)},
        )


# Module-level registry of named SlidingWindowRateLimiter instances so per-endpoint
# limiters are singletons (one window per name, not recreated per request).
_named_limiters: dict[str, SlidingWindowRateLimiter] = {}
_named_limiters_lock = threading.Lock()


def _get_or_create_limiter(name: str, *, calls: int, period: int = 60) -> SlidingWindowRateLimiter:
    with _named_limiters_lock:
        if name not in _named_limiters:
            _named_limiters[name] = SlidingWindowRateLimiter(calls=calls, period=period)
        return _named_limiters[name]


def reset_all_limiters() -> None:
    """Clear all in-memory rate-limit windows.

    Intended for use in test teardown / setup to prevent window state from one test
    bleeding into another.  Not safe to call in production under concurrent load.
    """
    with _named_limiters_lock:
        for limiter in _named_limiters.values():
            with limiter._lock:
                limiter._windows.clear()
        _named_limiters.clear()
