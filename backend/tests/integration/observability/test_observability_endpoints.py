"""Integration: observability HTTP endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_health_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_auth_diagnostics_hidden_by_default(client: TestClient) -> None:
    r = client.get("/internal/devnest-auth-diagnostics")
    assert r.status_code == 404


def test_ready_ok(client: TestClient) -> None:
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_metrics_exposes_devnest_series(client: TestClient) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    assert "# HELP" in body
    assert "# TYPE" in body
    assert "devnest_queue_depth" in body
    assert "devnest_jobs" in body or "devnest_jobs_queued" in body
    assert "devnest_workspace_states" in body
    assert "devnest_execution_nodes" in body
    assert "devnest_autoscaler_decisions" in body
    assert "devnest_workspace_provisioning_duration_seconds" in body


def test_correlation_id_round_trip_on_response(client: TestClient) -> None:
    r = client.get("/health", headers={"X-Correlation-ID": "integration-corr-xyz"})
    assert r.status_code == 200
    assert r.headers.get("X-Correlation-ID") == "integration-corr-xyz"
