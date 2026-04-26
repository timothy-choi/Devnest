"""Ensure ``POST /internal/execution-nodes/heartbeat`` is always on the mounted FastAPI app."""

from __future__ import annotations

from app.main import app


def test_main_app_exposes_post_internal_execution_nodes_heartbeat() -> None:
    matches = [
        r
        for r in app.routes
        if getattr(r, "path", "") == "/internal/execution-nodes/heartbeat" and "POST" in (getattr(r, "methods", None) or set())
    ]
    assert len(matches) >= 1
    assert len(matches) == 1, "expected exactly one POST heartbeat route (router + optional fallback must not duplicate)"
