"""Unit tests: reconcile decision helpers (no DB)."""

from __future__ import annotations

from app.services.reconcile_service.decisions import (
    gateway_route_needs_repair,
    normalize_http_target,
    route_row_for_workspace,
    targets_equivalent,
)


def test_normalize_http_target_adds_scheme() -> None:
    assert normalize_http_target("10.0.0.1:8080") == "http://10.0.0.1:8080"


def test_targets_equivalent_ignores_trailing_slash() -> None:
    assert targets_equivalent("http://10.0.0.1:8080", "http://10.0.0.1:8080/")
    assert targets_equivalent("10.0.0.1:8080", "http://10.0.0.1:8080")


def test_route_row_for_workspace() -> None:
    routes = [
        {"workspace_id": "2", "target": "http://x"},
        {"workspace_id": "7", "target": "http://y"},
    ]
    assert route_row_for_workspace(routes, 7)["target"] == "http://y"
    assert route_row_for_workspace(routes, 99) is None


def test_gateway_route_needs_repair_missing_row() -> None:
    assert gateway_route_needs_repair(route_row=None, observed_internal_endpoint="10.0.0.1:8080") is True


def test_gateway_route_needs_repair_wrong_target() -> None:
    row = {"workspace_id": "1", "target": "http://old:8080"}
    assert (
        gateway_route_needs_repair(
            route_row=row,
            observed_internal_endpoint="http://10.0.0.5:8080",
        )
        is True
    )


def test_gateway_route_needs_repair_ok() -> None:
    row = {"workspace_id": "1", "target": "http://10.0.0.5:8080"}
    assert (
        gateway_route_needs_repair(
            route_row=row,
            observed_internal_endpoint="10.0.0.5:8080",
        )
        is False
    )


def test_gateway_route_needs_repair_no_endpoint() -> None:
    assert gateway_route_needs_repair(route_row=None, observed_internal_endpoint=None) is False
