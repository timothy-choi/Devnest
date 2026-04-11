"""System tests: build ``Dockerfile.workspace`` and verify code-server over real HTTP (no mocks)."""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request

import docker
import pytest
from docker.models.containers import Container

from .conftest import _assert_port_listening, find_repo_root

pytestmark = [pytest.mark.system, pytest.mark.workspace_image]

# code-server can be slow to bind on cold start; image layers are already built.
_STARTUP_TIMEOUT_S = float(os.environ.get("DEVNEST_WORKSPACE_TEST_STARTUP_TIMEOUT", "240"))


def _wait_for_code_server_http(host: str, port: int, *, timeout_s: float) -> tuple[int, str]:
    deadline = time.monotonic() + timeout_s
    last: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            url = f"http://{host}:{port}/"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "devnest-workspace-system-test/1.0"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                status = resp.status
                body = resp.read(524_288).decode("utf-8", errors="replace")
                if status in (200, 301, 302, 303, 307, 308):
                    return status, body
        except urllib.error.HTTPError as e:
            # urllib raises for 4xx/5xx; treat auth responses as "server is up".
            if e.code in (401, 403):
                try:
                    b = e.read(524_288).decode("utf-8", errors="replace")
                except Exception:
                    b = ""
                return int(e.code), b
            last = e
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError) as e:
            last = e
        except Exception as e:
            last = e
        time.sleep(2)
    pytest.fail(f"code-server did not respond with an HTTP success within {timeout_s}s (last error: {last!r})")


def test_dockerfile_workspace_builds(built_workspace_image: str, docker_client: docker.DockerClient) -> None:
    """Image exists after session build (validates Dockerfile.workspace compiles)."""
    docker_client.images.get(built_workspace_image)


def test_repo_contains_dockerfile_workspace() -> None:
    root = find_repo_root()
    assert (root / "Dockerfile.workspace").is_file()


def test_workspace_container_code_server_http(
    running_workspace_container: tuple[Container, int, str],
) -> None:
    """Container runs, TCP port is open, and HTTP GET returns a plausible code-server page."""
    container, host_port, _workspace = running_workspace_container

    container.reload()
    assert container.status == "running"

    status, body = _wait_for_code_server_http(
        "127.0.0.1",
        host_port,
        timeout_s=_STARTUP_TIMEOUT_S,
    )
    assert status in (200, 301, 302, 303, 307, 308, 401, 403)

    _assert_port_listening("127.0.0.1", host_port, timeout_s=5.0)

    if status == 200:
        lower = body.lower()
        assert (
            "code-server" in lower
            or "coder" in lower
            or "<html" in lower
            or "vscode" in lower
        ), "200 response should look like code-server or HTML shell"
