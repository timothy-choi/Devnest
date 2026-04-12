"""Liveness, readiness, and Prometheus metrics (no auth — protect at ingress in production)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import text
from sqlmodel import Session

from app.libs.db.database import get_engine

from .metrics import metrics_response_body, refresh_gauges_from_db

router = APIRouter(tags=["observability"])


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
    description="Database reachable. TODO: optional dependency checks (Redis, gateway).",
)
def get_ready() -> dict[str, str]:
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"not_ready:{exc.__class__.__name__}",
        ) from exc
    return {"status": "ready"}


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    description=(
        "OpenMetrics/Prometheus exposition. Refreshes queue and entity-count gauges per scrape. "
        "TODO: secure behind internal network or bearer token at the edge."
    ),
)
def get_metrics() -> Response:
    engine = get_engine()
    with Session(engine) as session:
        refresh_gauges_from_db(session)
    body, media_type = metrics_response_body()
    return Response(content=body, media_type=media_type)
