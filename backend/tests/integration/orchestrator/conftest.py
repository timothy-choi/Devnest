"""Orchestrator integration: real Docker runtime + PostgreSQL topology (no host bridge/netns)."""

from __future__ import annotations

import os

import docker
import pytest
from sqlmodel import Session

from app.libs.runtime.docker_runtime import DockerRuntimeAdapter
from app.libs.topology import DbTopologyAdapter


@pytest.fixture(autouse=True)
def _skip_linux_topology_for_orchestrator_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Align with probe/topology DB integration: no CAP_NET_ADMIN on the pytest host."""
    monkeypatch.setenv("DEVNEST_TOPOLOGY_SKIP_LINUX_BRIDGE", "1")
    monkeypatch.setenv("DEVNEST_TOPOLOGY_SKIP_LINUX_ATTACHMENT", "1")


@pytest.fixture(scope="session")
def orchestrator_docker_client():
    """
    Real Docker engine for ``DockerRuntimeAdapter``.

    Skips when the daemon is unreachable (e.g. integration job without Docker socket).
    """
    try:
        client = docker.from_env()
        client.ping()
    except Exception as e:
        pytest.skip(f"Docker daemon required for orchestrator integration tests: {e}")
    yield client
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


@pytest.fixture(scope="session")
def orchestrator_integration_image(orchestrator_docker_client) -> str:
    """Image for workspace containers; default is lightweight and commonly cached in CI."""
    image = os.environ.get("DEVNEST_ORCHESTRATOR_INTEGRATION_IMAGE", "nginx:alpine").strip()
    if not image:
        image = "nginx:alpine"
    orchestrator_docker_client.images.pull(image)
    return image


@pytest.fixture
def topology_adapter_integration(db_session: Session) -> DbTopologyAdapter:
    return DbTopologyAdapter(
        db_session,
        apply_linux_bridge=False,
        apply_linux_attachment=False,
    )


@pytest.fixture
def runtime_adapter_integration(orchestrator_docker_client) -> DockerRuntimeAdapter:
    return DockerRuntimeAdapter(client=orchestrator_docker_client)
