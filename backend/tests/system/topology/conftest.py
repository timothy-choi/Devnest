"""Fixtures for real Linux bridge/veth topology tests (V1).

These tests run ``ip`` on the **pytest host**. They are skipped on non-Linux or without
``CAP_NET_ADMIN`` (unless you use ``sudo`` — see README).

Parent ``tests/system/conftest.py`` still requires a reachable Docker daemon for collection
under ``tests/system/``; attach-related tests additionally need a working ``docker run``.
"""

from __future__ import annotations

import platform
import subprocess
import uuid
from collections.abc import Generator

import docker
import docker.errors
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.libs.topology import DbTopologyAdapter


def _remove_container_force(client: docker.DockerClient, name: str) -> None:
    try:
        client.containers.get(name).remove(force=True)
    except docker.errors.NotFound:
        pass

LINUX = platform.system() == "Linux"


@pytest.fixture(autouse=True)
def _clear_topology_linux_skip_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real checks must not inherit unit-test skip flags from other conftests."""
    monkeypatch.delenv("DEVNEST_TOPOLOGY_SKIP_LINUX_BRIDGE", raising=False)
    monkeypatch.delenv("DEVNEST_TOPOLOGY_SKIP_LINUX_ATTACHMENT", raising=False)


@pytest.fixture(scope="module")
def linux_net_admin_or_skip() -> None:
    """One-time probe: can this uid create/delete a bridge?"""
    if not LINUX:
        pytest.skip("Topology V1 Linux tests require a Linux host (not Darwin/WSL host pytest).")
    name = f"dnst{uuid.uuid4().hex[:6]}"
    add = subprocess.run(
        ["ip", "link", "add", name, "type", "bridge"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if add.returncode != 0:
        pytest.skip(
            "Cannot create a test bridge (needs CAP_NET_ADMIN). "
            "Run: sudo pytest tests/system/topology -m 'topology_linux or topology_linux_core'\n"
            f"ip stderr: {add.stderr.strip()}",
        )
    subprocess.run(["ip", "link", "del", "dev", name], capture_output=True, timeout=10)


@pytest.fixture
def topology_sqlite_session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def linux_topology_adapter(
    topology_sqlite_session: Session,
    docker_client: docker.DockerClient,
) -> DbTopologyAdapter:
    def _pid_resolver(container_id: str) -> int | None:
        try:
            c = docker_client.containers.get(container_id)
            raw = (c.attrs.get("State") or {}).get("Pid")
            if isinstance(raw, int) and raw > 0:
                return raw
        except Exception:
            return None
        return None

    return DbTopologyAdapter(
        topology_sqlite_session,
        apply_linux_bridge=True,
        apply_linux_attachment=True,
        container_init_pid_resolver=_pid_resolver,
    )


@pytest.fixture
def alpine_netns_container(docker_client: docker.DockerClient) -> Generator[tuple[str, int], None, None]:
    """
    Running Alpine container; yield ``(container_name, init_pid)`` for ``/proc/<pid>/ns/net``.

    Pulls ``alpine:3.19`` on first use. Always removed in ``finally``.
    """
    name = f"devnest-topo-{uuid.uuid4().hex[:12]}"
    docker_client.images.pull("alpine:3.19")
    container = docker_client.containers.run(
        "alpine:3.19",
        command="sleep 240",
        detach=True,
        remove=True,
        name=name,
    )
    try:
        container.reload()
        pid = int(container.attrs["State"]["Pid"])
        if pid <= 0:
            pytest.fail(f"container {name!r} has invalid Pid={pid}")
        yield name, pid
    finally:
        _remove_container_force(docker_client, name)
