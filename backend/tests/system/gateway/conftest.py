"""Gateway system stack (Docker Compose) + control-plane fixtures."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

if TYPE_CHECKING:
    pass


def _repo_root() -> Path:
    p = Path(__file__).resolve().parent
    for _ in range(8):
        if (p / "docker-compose.system.yml").is_file():
            return p
        if p.parent == p:
            break
        p = p.parent
    pytest.fail("Could not find docker-compose.system.yml (repo root discovery failed)")


@pytest.fixture(scope="session")
def gateway_system_stack(docker_client) -> None:
    """
    Start Traefik + route-admin + workspace-sim for the whole session.

    Requires Docker; intended for ``tests/system/gateway`` (see CI job ``system-gateway-tests``).
    """
    root = _repo_root()
    compose = root / "docker-compose.system.yml"
    assert compose.is_file(), compose

    down = subprocess.run(
        ["docker", "compose", "-f", str(compose), "down", "-v"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    # ignore errors on pre-clean

    up = subprocess.run(
        ["docker", "compose", "-f", str(compose), "up", "-d", "--build"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert up.returncode == 0, up.stderr or up.stdout

    base = os.environ.get("ROUTE_ADMIN_SYSTEM_PORT", "19080")
    url = f"http://127.0.0.1:{base}/health"
    deadline = time.monotonic() + 120.0
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=5.0)
            if r.status_code == 200:
                break
        except Exception as e:
            last_err = e
        time.sleep(0.5)
    else:
        pytest.fail(f"route-admin not healthy at {url!r}: {last_err!r}")

    yield

    subprocess.run(
        ["docker", "compose", "-f", str(compose), "down", "-v"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


@pytest.fixture(autouse=True)
def _gateway_system_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the backend gateway client at the system route-admin; enable registration."""
    monkeypatch.setenv(
        "DEVNEST_GATEWAY_URL",
        f"http://127.0.0.1:{os.environ.get('ROUTE_ADMIN_SYSTEM_PORT', '19080')}",
    )
    monkeypatch.setenv("DEVNEST_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("DEVNEST_BASE_DOMAIN", "app.devnest.local")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _gateway_system_internal_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "system-gateway-integration-key")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
