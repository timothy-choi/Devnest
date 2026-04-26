"""Pure helpers for reconcile target/route comparison (unit-tested without DB)."""

from __future__ import annotations


def normalize_http_target(raw: str | None) -> str:
    t = (raw or "").strip()
    if not t:
        return ""
    if "://" not in t:
        t = f"http://{t}"
    if not (t.startswith("http://") or t.startswith("https://")):
        return ""
    return t.rstrip("/")


def targets_equivalent(a: str | None, b: str | None) -> bool:
    return normalize_http_target(a) == normalize_http_target(b)


def route_row_for_workspace(routes: list[dict], workspace_id: int) -> dict | None:
    sid = str(workspace_id)
    for row in routes:
        if str(row.get("workspace_id") or "") == sid:
            return row
    return None


def _norm_public_host(raw: str | None) -> str:
    return (raw or "").strip().lower()


def gateway_route_needs_repair(
    *,
    route_row: dict | None,
    observed_internal_endpoint: str | None,
    expected_public_host: str | None = None,
) -> bool:
    """``observed_internal_endpoint`` is the desired route-admin ``target`` (Traefik upstream), not necessarily the topology IP."""
    ep = (observed_internal_endpoint or "").strip()
    if not ep:
        return False
    expected = normalize_http_target(ep)
    if not expected:
        return False
    if route_row is None:
        return True
    if not targets_equivalent(route_row.get("target"), observed_internal_endpoint):
        return True
    want = _norm_public_host(expected_public_host)
    if want and _norm_public_host(str(route_row.get("public_host") or "")) != want:
        return True
    return False
