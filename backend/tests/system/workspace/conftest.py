"""Fixtures for workspace image system tests (real Docker build + run).

Control-plane + Postgres + ``TestClient`` fixtures (``workspace_control_plane_*``) support
``test_workspace_events_control_plane_system.py``. They mirror ``tests/integration/workspace/conftest.py``
so Pytest 8+ does not need nested ``pytest_plugins`` across ``tests/system`` vs ``tests/integration``.
"""

from __future__ import annotations

import os
import shutil
import socket
import time
import uuid
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import docker
import docker.errors
import pytest
from docker.models.containers import Container
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.libs.topology.models import Topology

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


# --- Control-plane E2E (Postgres + API + Docker orchestrator path) -----------------------------


@pytest.fixture(scope="session")
def workspace_control_plane_postgres_engine() -> Generator[Engine, None, None]:
    """Single shared engine for ``tests/system/workspace`` control-plane tests (CI ``DATABASE_URL``)."""
    from app.libs.common.config import get_settings
    from app.libs.db.database import get_engine, init_db, reset_engine

    get_settings.cache_clear()
    reset_engine()
    engine = get_engine()
    init_db()
    yield engine
    engine.dispose()


@pytest.fixture
def workspace_control_plane_db_session(workspace_control_plane_postgres_engine: Engine) -> Generator[Session, None, None]:
    with Session(workspace_control_plane_postgres_engine) as session:
        yield session


@pytest.fixture
def workspace_control_plane_client(workspace_control_plane_postgres_engine: Engine) -> Generator[TestClient, None, None]:
    from app.main import app
    from app.services.auth_service.api.dependencies import get_db

    def override_get_db():
        db = Session(workspace_control_plane_postgres_engine)
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture(name="db_session")
def workspace_control_plane_db_session_alias(workspace_control_plane_db_session: Session) -> Generator[Session, None, None]:
    yield workspace_control_plane_db_session


@pytest.fixture(name="client")
def workspace_control_plane_client_alias(workspace_control_plane_client: TestClient) -> Generator[TestClient, None, None]:
    yield workspace_control_plane_client


@pytest.fixture
def _workspace_control_plane_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Orchestrator-friendly defaults: no host bridge/veth, lightweight workspace image."""
    monkeypatch.setenv("DEVNEST_TOPOLOGY_SKIP_LINUX_BRIDGE", "1")
    monkeypatch.setenv("DEVNEST_TOPOLOGY_SKIP_LINUX_ATTACHMENT", "1")
    monkeypatch.setenv("WORKSPACE_CONTAINER_IMAGE", "nginx:alpine")
    from app.libs.common.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def workspace_control_plane_topology(
    workspace_control_plane_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> int:
    """Persist ``Topology`` and set ``DEVNEST_TOPOLOGY_ID`` for ``DbTopologyAdapter``."""
    oct2 = (uuid.uuid4().int % 200) + 1
    cidr = f"10.{oct2}.0.0/24"
    gateway = f"10.{oct2}.0.1"
    t = Topology(
        name=f"sys-cp-{uuid.uuid4().hex[:8]}",
        version="v1",
        spec_json={
            "cidr": cidr,
            "gateway_ip": gateway,
            "bridge_name": f"brcp{oct2 % 900 + 100}"[:15],
        },
    )
    workspace_control_plane_db_session.add(t)
    workspace_control_plane_db_session.commit()
    workspace_control_plane_db_session.refresh(t)
    assert t.topology_id is not None
    monkeypatch.setenv("DEVNEST_TOPOLOGY_ID", str(t.topology_id))
    return t.topology_id


@pytest.fixture
def workspace_control_plane_probe_socket_patch() -> Generator[None, None, None]:
    """Stub TCP connect for service probes (workspace IP is not host-routable)."""

    class _FakeSock:
        def close(self) -> None:
            pass

    with patch(
        "app.libs.probes.probe_runner.socket.create_connection",
        return_value=_FakeSock(),
    ):
        yield
