"""HTTP middleware: correlation id on ``request.state`` + contextvar + response header."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .correlation import reset_correlation_id, resolve_correlation_id, set_correlation_id


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Reads ``X-Correlation-ID`` or ``X-Request-ID``, stores on ``request.state.correlation_id``,
    binds contextvar for async code, echoes ``X-Correlation-ID`` on the response.

    Sync route handlers should read ``request.state.correlation_id`` (contextvars may not cross
    thread-pool boundaries reliably).
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")
        cid = resolve_correlation_id(incoming)
        request.state.correlation_id = cid
        token = set_correlation_id(cid)
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = cid
            return response
        finally:
            reset_correlation_id(token)
