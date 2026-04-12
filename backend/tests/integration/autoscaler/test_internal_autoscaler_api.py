"""Integration: internal autoscaler routes (auth + JSON shape only)."""

from __future__ import annotations

import os

import pytest
from fastapi import status
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _headers() -> dict[str, str]:
    key = os.environ.get("INTERNAL_API_KEY", "")
    assert key
    return {"X-Internal-API-Key": key}


def test_get_autoscaler_evaluate_returns_scale_decisions(client: TestClient) -> None:
    r = client.get("/internal/autoscaler/evaluate", headers=_headers())
    assert r.status_code == status.HTTP_200_OK, r.text
    data = r.json()
    assert "scale_up" in data and "scale_down" in data
    assert "should_provision" in data["scale_up"]
    assert "node_key" in data["scale_down"]
