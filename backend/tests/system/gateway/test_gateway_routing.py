"""System tests: Traefik forwards HTTP to an upstream on the system Docker network.

This validates the **data plane** path (route-admin + Traefik + upstream). Workspace runtimes
started by the orchestrator use topology IPs that are not reachable from the system Traefik
container on default CI networking; control-plane registration of those targets is covered in
``test_backend_gateway_integration.py``.
"""

from __future__ import annotations

import time

import httpx
import pytest

from . import helpers

pytestmark = [
    pytest.mark.system,
    pytest.mark.gateway,
    pytest.mark.slow,
    pytest.mark.usefixtures("gateway_system_stack", "docker_client"),
]


def test_traefik_proxies_registered_route_to_workspace_sim() -> None:
    base = helpers.route_admin_base_url()
    pub = helpers.traefik_public_url()
    host = "sim-route.app.devnest.local"

    r = httpx.post(
        f"{base}/routes",
        json={
            "workspace_id": "sim-route",
            "public_host": host,
            "target": "http://workspace-sim:80",
        },
        timeout=30.0,
    )
    assert r.status_code == 200, r.text

    deadline = time.monotonic() + 60.0
    last: str | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(
                pub,
                headers={"Host": host},
                timeout=10.0,
            )
            if resp.status_code == 200 and "DEVNEST_SYSTEM_WORKSPACE_SIM_OK" in resp.text:
                return
            last = f"{resp.status_code} {resp.text[:200]}"
        except Exception as e:
            last = str(e)
        time.sleep(0.5)

    pytest.fail(f"Traefik did not proxy to workspace-sim: {last}")
