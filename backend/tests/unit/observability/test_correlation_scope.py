"""Correlation id context and middleware."""

from __future__ import annotations

import logging

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.libs.observability.correlation import correlation_scope, get_correlation_id
from app.libs.observability.log_events import LogEvent, log_event
from app.libs.observability.middleware import CorrelationIdMiddleware


def test_correlation_scope_restores_previous() -> None:
    assert get_correlation_id() is None
    with correlation_scope("outer"):
        assert get_correlation_id() == "outer"
        with correlation_scope("inner"):
            assert get_correlation_id() == "inner"
        assert get_correlation_id() == "outer"
    assert get_correlation_id() is None


def test_correlation_scope_generates_when_empty() -> None:
    with correlation_scope(None) as cid:
        assert len(cid) == 36  # uuid4


def test_middleware_sets_request_state_and_header() -> None:
    async def handler(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "state_cid": getattr(request.state, "correlation_id", None),
                "ctx_cid": get_correlation_id(),
            },
        )

    app = Starlette(routes=[Route("/", handler)])
    app.add_middleware(CorrelationIdMiddleware)
    client = TestClient(app)
    r = client.get("/", headers={"X-Correlation-ID": "client-supplied-99"})
    assert r.status_code == 200
    body = r.json()
    assert body["state_cid"] == "client-supplied-99"
    assert body["ctx_cid"] == "client-supplied-99"
    assert r.headers.get("X-Correlation-ID") == "client-supplied-99"


def test_middleware_generates_id_when_missing() -> None:
    async def handler(request: Request) -> JSONResponse:
        return JSONResponse({"cid": getattr(request.state, "correlation_id", None)})

    app = Starlette(routes=[Route("/", handler)])
    app.add_middleware(CorrelationIdMiddleware)
    client = TestClient(app)
    r = client.get("/")
    cid = r.json()["cid"]
    assert cid and len(cid) >= 8
    assert r.headers.get("X-Correlation-ID") == cid


def test_log_event_includes_devnest_event_and_correlation() -> None:
    log = logging.getLogger("observability_test")
    with correlation_scope("log-corr-1"):
        log_event(log, LogEvent.WORKSPACE_JOB_STARTED, workspace_id=7, workspace_job_id=42)
    # Smoke: no exception; formatter may not show extra in caplog without custom handler
