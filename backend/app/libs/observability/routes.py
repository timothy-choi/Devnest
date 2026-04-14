"""Liveness, readiness, and Prometheus metrics.

The ``/metrics`` endpoint is optionally protected by an internal API key
(``DEVNEST_METRICS_AUTH_ENABLED=true``). When enabled it requires the
``X-Internal-API-Key`` header validated against the INFRASTRUCTURE scope.
When disabled (default) access is open; protect at the ingress layer in production.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Response, status
from sqlalchemy import text
from sqlmodel import Session

from app.libs.common.config import get_settings
from app.libs.db.database import get_engine
from app.libs.observability.log_events import LogEvent, log_event
from app.libs.security.internal_auth import InternalApiScope, internal_api_key_is_valid

from .metrics import metrics_response_body, refresh_gauges_from_db

router = APIRouter(tags=["observability"])

_logger = logging.getLogger(__name__)


@router.get(
    "/health",
    summary="Liveness",
    description="Process is up. Does not check dependencies.",
)
def get_health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/ready",
    summary="Readiness",
    description=(
        "Verifies all required dependencies are reachable. "
        "Checks: database connectivity. "
        "Optional checks (when configured): Redis connectivity. "
        "Returns 200 when all checks pass; 503 with a structured failure report otherwise."
    ),
)
def get_ready() -> dict:
    checks: dict[str, str] = {}
    failures: list[str] = []

    # --- Database ---
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error:{exc.__class__.__name__}"
        failures.append("database")

    # --- Redis (optional, only when URL is configured) ---
    settings = get_settings()
    redis_url = (getattr(settings, "devnest_redis_url", "") or "").strip()
    if redis_url:
        try:
            import redis as _redis  # noqa: PLC0415
            r = _redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
            r.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error:{exc.__class__.__name__}"
            failures.append("redis")
    else:
        checks["redis"] = "not_configured"

    if failures:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "failed": failures, "checks": checks},
        )
    return {"status": "ready", "checks": checks}


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    description=(
        "OpenMetrics/Prometheus exposition. Refreshes queue and entity-count gauges per scrape. "
        "Includes ``devnest_internal_auth_failures_total`` (per internal surface scope). "
        "Protected by ``X-Internal-API-Key`` when ``DEVNEST_METRICS_AUTH_ENABLED=true``."
    ),
)
def get_metrics(
    x_internal_api_key: str | None = Header(default=None, alias="X-Internal-API-Key"),
) -> Response:
    settings = get_settings()
    if settings.devnest_metrics_auth_enabled:
        if not internal_api_key_is_valid(x_internal_api_key, settings, InternalApiScope.INFRASTRUCTURE):
            log_event(
                _logger,
                LogEvent.SECURITY_INTERNAL_AUTH_FAILED,
                level=logging.WARNING,
                internal_scope="metrics",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing internal API key for metrics endpoint",
            )
    engine = get_engine()
    with Session(engine) as session:
        refresh_gauges_from_db(session)
    body, media_type = metrics_response_body()
    return Response(content=body, media_type=media_type)
