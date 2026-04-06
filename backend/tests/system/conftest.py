"""System tests: real external dependencies (Docker). See ``pytest.ini`` marker ``system``."""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Generator
import docker
import docker.errors
import pytest

from app.libs.runtime.docker_runtime import DockerRuntimeAdapter

from .isolated_context import IsolatedRuntimeContext


def _default_system_image() -> str:
    return os.environ.get("DEVNEST_RUNTIME_SYSTEM_IMAGE", "nginx:alpine").strip() or "nginx:alpine"


def _remove_container_force(client: docker.DockerClient, name: str) -> None:
    try:
        c = client.containers.get(name)
        c.remove(force=True)
    except docker.errors.NotFound:
        pass


@pytest.fixture(scope="session", autouse=True)
def _require_docker_daemon() -> None:
    """System tests hard-fail when the Docker engine is unreachable (no silent skip)."""
    try:
        docker.from_env().ping()
    except Exception as e:
        pytest.fail(f"Docker daemon required for tests/system but unreachable: {e}")


@pytest.fixture(scope="session")
def docker_client() -> docker.DockerClient:
    return docker.from_env()


@pytest.fixture(scope="session")
def system_test_image(docker_client: docker.DockerClient) -> str:
    """Lightweight image with a long-running default CMD (override via ``DEVNEST_RUNTIME_SYSTEM_IMAGE``)."""
    image = _default_system_image()
    docker_client.images.pull(image)
    return image


@pytest.fixture
def isolated_runtime(
    docker_client: docker.DockerClient,
    system_test_image: str,
) -> Generator[IsolatedRuntimeContext, None, None]:
    """
    Unique name, temp bind-mount dir, teardown removes container + dir.

    Port publishing uses the adapter default (ephemeral host port for container 8080) to avoid
    fixed host-port collisions across parallel runs or local services.
    """
    name = f"devnest-sys-{uuid.uuid4().hex[:12]}"
    workspace = os.path.join(
        os.environ.get("TMPDIR", "/tmp"),
        f"devnest-runtime-{uuid.uuid4().hex[:12]}",
    )
    os.makedirs(workspace, mode=0o755, exist_ok=False)
    adapter = DockerRuntimeAdapter(client=docker_client)
    ctx = IsolatedRuntimeContext(
        adapter=adapter,
        name=name,
        workspace_host_path=workspace,
        image=system_test_image,
    )
    try:
        yield ctx
    finally:
        _remove_container_force(docker_client, name)
        shutil.rmtree(workspace, ignore_errors=True)
