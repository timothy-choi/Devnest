"""System tests: real external dependencies (Docker). See ``pytest.ini`` marker ``system``."""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Generator
from unittest.mock import patch

import docker
import docker.errors
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.libs.runtime.docker_runtime import DockerRuntimeAdapter
from app.libs.topology.models import Topology

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

    Runtime tests that need a host mapping pass explicit ``ports`` (see ``tests/system/runtime``).
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


# --- Control-plane (Postgres + API + orchestrator): workspace + gateway system tests ----------


@pytest.fixture(scope="session")
def workspace_control_plane_postgres_engine() -> Generator[Engine, None, None]:
    """Shared engine for system control-plane tests (CI ``DATABASE_URL``)."""
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
