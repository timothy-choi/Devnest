"""Fixtures for workspace image system tests (real Docker build + run).

Control-plane fixtures live in ``tests/system/conftest.py`` (shared with ``tests/system/gateway``).
"""

from __future__ import annotations

import os
import shutil
import socket
import time
import uuid
from collections.abc import Generator
from pathlib import Path

import docker
import docker.errors
import pytest
from docker.models.containers import Container

from tests.system.conftest import _remove_container_force

WORKSPACE_IMAGE_TAG = os.environ.get("DEVNEST_WORKSPACE_TEST_IMAGE_TAG", "devnest-workspace-test:latest")


def find_repo_root() -> Path:
    p = Path(__file__).resolve().parent
    for _ in range(10):
        if (p / "Dockerfile.workspace").is_file():
            return p
        if p.parent == p:
            break
        p = p.parent
    pytest.fail("Could not find Dockerfile.workspace (repo root discovery failed)")


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _assert_port_listening(host: str, port: int, timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    last: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError as e:
            last = e
            time.sleep(0.1)
    pytest.fail(f"port {port} not accepting connections: {last}")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return find_repo_root()


@pytest.fixture(scope="session")
def built_workspace_image(docker_client: docker.DockerClient, repo_root: Path) -> Generator[str, None, None]:
    """
    Build ``Dockerfile.workspace`` once per session.

    Set ``DEVNEST_WORKSPACE_TEST_KEEP_IMAGE=1`` to skip ``docker rmi`` after the session (faster local re-runs).
    """
    tag = WORKSPACE_IMAGE_TAG
    _, build_logs = docker_client.images.build(
        path=str(repo_root),
        dockerfile="Dockerfile.workspace",
        tag=tag,
        rm=True,
        forcerm=True,
    )
    for _ in build_logs:
        pass

    docker_client.images.get(tag)
    yield tag

    if os.environ.get("DEVNEST_WORKSPACE_TEST_KEEP_IMAGE", "").lower() in ("1", "true", "yes"):
        return
    try:
        docker_client.images.remove(tag, force=True)
    except docker.errors.ImageNotFound:
        pass


@pytest.fixture
def running_workspace_container(
    docker_client: docker.DockerClient,
    built_workspace_image: str,
) -> Generator[tuple[Container, int, str], None, None]:
    """
    Start a container from the built workspace image, map container 8080 to a random host port,
    bind-mount an empty project dir, always stop/remove in ``finally``.
    """
    import tempfile

    host_port = _free_tcp_port()
    name = f"devnest-ws-{uuid.uuid4().hex[:12]}"
    workspace = tempfile.mkdtemp(prefix="devnest-ws-proj-")
    container: Container | None = None
    try:
        container = docker_client.containers.run(
            built_workspace_image,
            detach=True,
            name=name,
            ports={"8080/tcp": host_port},
            volumes={workspace: {"bind": "/home/coder/project", "mode": "rw"}},
        )
        yield container, host_port, workspace
    finally:
        _remove_container_force(docker_client, name)
        shutil.rmtree(workspace, ignore_errors=True)
