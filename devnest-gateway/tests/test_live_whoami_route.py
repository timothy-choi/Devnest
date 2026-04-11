"""
Lightweight integration: stack up, Host-routed request to mock-upstream, stack down.

Requires Docker (same as docker compose config test). Uses host port 80; skips if unavailable.
"""

import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


def _port_free(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return False
    except OSError:
        return True


@pytest.mark.integration
def test_whoami_hostname_routes_through_traefik(gateway_root: Path) -> None:
    if not shutil.which("docker"):
        pytest.skip("docker not on PATH")
    if not _port_free("127.0.0.1", 80):
        pytest.skip("port 80 in use (cannot bind gateway for this test)")

    try:
        up = subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=gateway_root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert up.returncode == 0, up.stderr or up.stdout

        last_err: Exception | None = None
        body = ""
        for _ in range(45):
            try:
                req = urllib.request.Request(
                    "http://127.0.0.1/",
                    headers={"Host": "whoami.app.devnest.local"},
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=3) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                assert resp.status == 200
                assert "whoami.app.devnest.local" in body
                break
            except (urllib.error.URLError, TimeoutError, AssertionError) as e:
                last_err = e
                time.sleep(1)
        else:
            pytest.fail(f"traefik/upstream not ready: {last_err!r}")

    finally:
        subprocess.run(
            ["docker", "compose", "down", "-v"],
            cwd=gateway_root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
