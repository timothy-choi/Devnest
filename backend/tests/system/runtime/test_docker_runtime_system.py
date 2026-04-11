"""
System tests: ``DockerRuntimeAdapter`` against a real Docker engine (no mocks, no HTTP API).

Isolation: each test gets a unique container name and temp workspace directory; the
``isolated_runtime`` fixture removes the container and directory in ``finally`` even when
a test fails.

**How to run only these tests** (from ``backend/``)::

    pytest tests/system/runtime/ -v -m system

Runtime adapter + real workspace image (code-server HTTP; slower)::

    pytest tests/system/workspace/test_runtime_adapter_workspace_system.py -v -m "system and workspace_image"

Or by path (skips collection of unrelated modules)::

    pytest tests/system/runtime/test_docker_runtime_system.py -v

Image: set ``DEVNEST_RUNTIME_SYSTEM_IMAGE`` to override the default ``nginx:alpine``
(see ``tests/system/conftest.py``). Tests pass ``ports=((0, WORKSPACE_IDE_CONTAINER_PORT),)``
so the engine assigns an ephemeral **host** port for the IDE container port (no fixed host 8080).

Coverage checklist: ``ensure_container``, ``start_container``, ``stop_container``, ``restart_container``,
``delete_container``, ``inspect_container``, ``get_container_netns_ref``, project mount persistence,
ephemeral/pinned host publish (no implicit shared host 8080). Real code-server HTTP is validated in
``tests/system/workspace/test_runtime_adapter_workspace_system.py`` (workspace image; ``workspace_image`` marker).
"""

from __future__ import annotations

import os
import shutil
import socket
import uuid

import docker
import pytest

from app.libs.runtime.docker_runtime import DockerRuntimeAdapter
from app.libs.runtime.errors import NetnsRefError
from app.libs.runtime.models import (
    CODE_SERVER_CONFIG_CONTAINER_PATH,
    CODE_SERVER_DATA_CONTAINER_PATH,
    WORKSPACE_IDE_CONTAINER_PORT,
    WORKSPACE_PROJECT_CONTAINER_PATH,
    WorkspaceExtraBindMountSpec,
)

from tests.system.conftest import _remove_container_force

from ..isolated_context import IsolatedRuntimeContext


pytestmark = pytest.mark.system


def _ensure(isolated_runtime: IsolatedRuntimeContext):
    """Create via adapter; ephemeral host publish for ``WORKSPACE_IDE_CONTAINER_PORT`` (host port from engine)."""
    return isolated_runtime.adapter.ensure_container(
        name=isolated_runtime.name,
        image=isolated_runtime.image,
        workspace_host_path=isolated_runtime.workspace_host_path,
        ports=((0, WORKSPACE_IDE_CONTAINER_PORT),),
        labels={"devnest.system_test": "runtime"},
    )


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _make_isolated_context(docker_client: docker.DockerClient, system_test_image: str) -> IsolatedRuntimeContext:
    name = f"devnest-sys-{uuid.uuid4().hex[:12]}"
    workspace = os.path.join(
        os.environ.get("TMPDIR", "/tmp"),
        f"devnest-runtime-{uuid.uuid4().hex[:12]}",
    )
    os.makedirs(workspace, mode=0o755, exist_ok=False)
    return IsolatedRuntimeContext(
        adapter=DockerRuntimeAdapter(client=docker_client),
        name=name,
        workspace_host_path=workspace,
        image=system_test_image,
    )


def test_ensure_container_creates_real_container(isolated_runtime: IsolatedRuntimeContext) -> None:
    r = _ensure(isolated_runtime)

    assert r.exists is True
    assert r.created_new is True
    assert r.container_id
    assert isinstance(r.resolved_ports, tuple)


def test_start_container_starts_real_container(isolated_runtime: IsolatedRuntimeContext) -> None:
    ensured = _ensure(isolated_runtime)
    started = isolated_runtime.adapter.start_container(container_id=ensured.container_id)

    assert started.success is True
    assert started.container_state == "running"


def test_ensure_container_second_call_reuses_existing(
    isolated_runtime: IsolatedRuntimeContext,
) -> None:
    first = _ensure(isolated_runtime)
    second = isolated_runtime.adapter.ensure_container(
        name=isolated_runtime.name,
        image=isolated_runtime.image,
        workspace_host_path=isolated_runtime.workspace_host_path,
        ports=((0, WORKSPACE_IDE_CONTAINER_PORT),),
        labels={"devnest.system_test": "runtime"},
    )

    assert first.created_new is True
    assert second.created_new is False
    assert second.container_id == first.container_id
    assert second.exists is True


def test_inspect_after_ensure_before_start_returns_normalized_snapshot(
    isolated_runtime: IsolatedRuntimeContext,
) -> None:
    ensured = _ensure(isolated_runtime)

    ins = isolated_runtime.adapter.inspect_container(container_id=ensured.container_id)

    assert ins.exists is True
    assert ins.container_id == ensured.container_id
    assert ins.container_state in ("created", "exited")
    assert isolated_runtime.workspace_host_path in "".join(ins.mounts)
    assert ins.workspace_project_mount is not None


def test_inspect_container_returns_normalized_real_data(
    isolated_runtime: IsolatedRuntimeContext,
) -> None:
    ensured = _ensure(isolated_runtime)
    isolated_runtime.adapter.start_container(container_id=ensured.container_id)

    ins = isolated_runtime.adapter.inspect_container(container_id=ensured.container_id)

    assert ins.exists is True
    assert ins.container_id
    assert ins.container_state == "running"
    assert ins.container_id == ensured.container_id
    assert ins.ports
    pair = ins.ports[0]
    assert isinstance(pair[0], int) and isinstance(pair[1], int)
    assert pair[1] == WORKSPACE_IDE_CONTAINER_PORT
    assert isolated_runtime.workspace_host_path in "".join(ins.mounts)


def test_stop_container_stops_running_container(isolated_runtime: IsolatedRuntimeContext) -> None:
    ensured = _ensure(isolated_runtime)
    isolated_runtime.adapter.start_container(container_id=ensured.container_id)

    stopped = isolated_runtime.adapter.stop_container(container_id=ensured.container_id)

    assert stopped.success is True
    assert stopped.container_state in ("exited", "created")


def test_stop_container_is_idempotent_when_already_stopped(
    isolated_runtime: IsolatedRuntimeContext,
) -> None:
    ensured = _ensure(isolated_runtime)
    isolated_runtime.adapter.start_container(container_id=ensured.container_id)
    isolated_runtime.adapter.stop_container(container_id=ensured.container_id)

    second = isolated_runtime.adapter.stop_container(container_id=ensured.container_id)

    assert second.success is True
    assert second.container_state in ("exited", "created", "missing")


def test_restart_container_restarts_container(isolated_runtime: IsolatedRuntimeContext) -> None:
    ensured = _ensure(isolated_runtime)
    isolated_runtime.adapter.start_container(container_id=ensured.container_id)

    restarted = isolated_runtime.adapter.restart_container(container_id=ensured.container_id)

    assert restarted.success is True
    assert restarted.container_state == "running"


def test_restart_container_preserves_engine_container_id(
    isolated_runtime: IsolatedRuntimeContext,
) -> None:
    ensured = _ensure(isolated_runtime)
    isolated_runtime.adapter.start_container(container_id=ensured.container_id)
    cid = ensured.container_id

    restarted = isolated_runtime.adapter.restart_container(container_id=cid)

    assert restarted.success is True
    after = isolated_runtime.adapter.inspect_container(container_id=cid)
    assert after.container_id == cid


def test_delete_container_removes_container(isolated_runtime: IsolatedRuntimeContext) -> None:
    ensured = _ensure(isolated_runtime)
    isolated_runtime.adapter.start_container(container_id=ensured.container_id)

    deleted = isolated_runtime.adapter.delete_container(container_id=ensured.container_id)

    assert deleted.success is True
    assert deleted.container_state == "missing"

    after = isolated_runtime.adapter.inspect_container(container_id=ensured.container_id)
    assert after.exists is False
    assert after.container_state == "missing"


def test_delete_container_idempotent_when_already_removed(
    isolated_runtime: IsolatedRuntimeContext,
) -> None:
    ensured = _ensure(isolated_runtime)
    isolated_runtime.adapter.start_container(container_id=ensured.container_id)
    first = isolated_runtime.adapter.delete_container(container_id=ensured.container_id)
    assert first.success is True

    second = isolated_runtime.adapter.delete_container(container_id=ensured.container_id)

    assert second.success is True
    assert second.container_state == "missing"


def test_full_lifecycle_ensure_start_inspect_netns_stop_restart_delete(
    isolated_runtime: IsolatedRuntimeContext,
) -> None:
    """Single-container smoke: adapter methods end-to-end (fixture still cleans up if an assert fails mid-way)."""
    ensured = _ensure(isolated_runtime)
    assert ensured.created_new is True

    started = isolated_runtime.adapter.start_container(container_id=ensured.container_id)
    assert started.success is True

    ins_run = isolated_runtime.adapter.inspect_container(container_id=ensured.container_id)
    assert ins_run.container_state == "running"

    ref = isolated_runtime.adapter.get_container_netns_ref(container_id=ensured.container_id)
    assert ref.netns_ref == f"/proc/{ref.pid}/ns/net"

    stopped = isolated_runtime.adapter.stop_container(container_id=ensured.container_id)
    assert stopped.success is True

    restarted = isolated_runtime.adapter.restart_container(container_id=ensured.container_id)
    assert restarted.success is True
    assert restarted.container_state == "running"

    deleted = isolated_runtime.adapter.delete_container(container_id=ensured.container_id)
    assert deleted.success is True

    final = isolated_runtime.adapter.inspect_container(container_id=ensured.container_id)
    assert final.exists is False
    assert final.container_state == "missing"


def test_explicit_pinned_host_port_not_implicit_8080(
    docker_client: docker.DockerClient,
    system_test_image: str,
) -> None:
    """Caller-chosen host port works; validates host publish is opt-in and not tied to a default host 8080."""
    ctx = _make_isolated_context(docker_client, system_test_image)
    label = {"devnest.system_test": "runtime-pin"}
    host_p = _free_tcp_port()
    if host_p == WORKSPACE_IDE_CONTAINER_PORT:
        host_p = _free_tcp_port()
    try:
        ensured = ctx.adapter.ensure_container(
            name=ctx.name,
            image=ctx.image,
            workspace_host_path=ctx.workspace_host_path,
            ports=((host_p, WORKSPACE_IDE_CONTAINER_PORT),),
            labels=label,
        )
        ctx.adapter.start_container(container_id=ensured.container_id)
        ins = ctx.adapter.inspect_container(container_id=ensured.container_id)
        assert ins.ports
        assert ins.ports[0] == (host_p, WORKSPACE_IDE_CONTAINER_PORT)
    finally:
        _remove_container_force(docker_client, ctx.name)
        shutil.rmtree(ctx.workspace_host_path, ignore_errors=True)


def test_two_parallel_containers_use_distinct_ephemeral_host_ports(
    docker_client: docker.DockerClient,
    system_test_image: str,
) -> None:
    """Each container asks for engine-assigned host ports; bindings must not collapse to one shared host port."""
    a = _make_isolated_context(docker_client, system_test_image)
    b = _make_isolated_context(docker_client, system_test_image)
    label = {"devnest.system_test": "runtime-multiport"}
    try:
        ra = a.adapter.ensure_container(
            name=a.name,
            image=a.image,
            workspace_host_path=a.workspace_host_path,
            ports=((0, WORKSPACE_IDE_CONTAINER_PORT),),
            labels=label,
        )
        rb = b.adapter.ensure_container(
            name=b.name,
            image=b.image,
            workspace_host_path=b.workspace_host_path,
            ports=((0, WORKSPACE_IDE_CONTAINER_PORT),),
            labels=label,
        )
        a.adapter.start_container(container_id=ra.container_id)
        b.adapter.start_container(container_id=rb.container_id)
        ia = a.adapter.inspect_container(container_id=ra.container_id)
        ib = b.adapter.inspect_container(container_id=rb.container_id)
        assert ia.ports and ib.ports
        host_a = ia.ports[0][0]
        host_b = ib.ports[0][0]
        assert host_a != host_b
        assert ia.ports[0][1] == WORKSPACE_IDE_CONTAINER_PORT
        assert ib.ports[0][1] == WORKSPACE_IDE_CONTAINER_PORT
    finally:
        _remove_container_force(docker_client, a.name)
        _remove_container_force(docker_client, b.name)
        shutil.rmtree(a.workspace_host_path, ignore_errors=True)
        shutil.rmtree(b.workspace_host_path, ignore_errors=True)


def test_project_bind_mount_persists_host_and_container_writes(
    isolated_runtime: IsolatedRuntimeContext,
    docker_client: docker.DockerClient,
) -> None:
    ws = isolated_runtime.workspace_host_path
    with open(os.path.join(ws, "seed.txt"), "w", encoding="utf-8") as f:
        f.write("from-host\n")

    ensured = _ensure(isolated_runtime)
    isolated_runtime.adapter.start_container(container_id=ensured.container_id)
    ctr = docker_client.containers.get(ensured.container_id)
    inner_seed = f"{WORKSPACE_PROJECT_CONTAINER_PATH}/seed.txt"
    inner_out = f"{WORKSPACE_PROJECT_CONTAINER_PATH}/from_container.txt"
    code, out = ctr.exec_run(
        f"sh -c 'test -f {inner_seed} && echo persisted > {inner_out}'",
        demux=False,
    )
    assert code == 0, out.decode("utf-8", errors="replace")

    isolated_runtime.adapter.stop_container(container_id=ensured.container_id)

    host_written = os.path.join(ws, "from_container.txt")
    assert os.path.isfile(host_written)
    with open(host_written, encoding="utf-8") as f:
        assert f.read().strip() == "persisted"


def test_extra_bind_code_server_paths_persist_on_host(
    isolated_runtime: IsolatedRuntimeContext,
    docker_client: docker.DockerClient,
) -> None:
    """Optional persistence: both paths in ``CODE_SERVER_OPTIONAL_PERSISTENCE_CONTAINER_PATHS`` (adapter never adds them implicitly)."""
    tmp_parent = os.path.dirname(isolated_runtime.workspace_host_path)
    cfg_h = os.path.join(tmp_parent, f"devnest-cs-cfg-{uuid.uuid4().hex[:10]}")
    data_h = os.path.join(tmp_parent, f"devnest-cs-dat-{uuid.uuid4().hex[:10]}")
    try:
        os.makedirs(cfg_h, mode=0o755, exist_ok=False)
        os.makedirs(data_h, mode=0o755, exist_ok=False)

        ensured = isolated_runtime.adapter.ensure_container(
            name=isolated_runtime.name,
            image=isolated_runtime.image,
            workspace_host_path=isolated_runtime.workspace_host_path,
            ports=((0, WORKSPACE_IDE_CONTAINER_PORT),),
            labels={"devnest.system_test": "runtime-cs-extra"},
            extra_bind_mounts=(
                WorkspaceExtraBindMountSpec(host_path=cfg_h, container_path=CODE_SERVER_CONFIG_CONTAINER_PATH),
                WorkspaceExtraBindMountSpec(host_path=data_h, container_path=CODE_SERVER_DATA_CONTAINER_PATH),
            ),
        )
        isolated_runtime.adapter.start_container(container_id=ensured.container_id)
        ctr = docker_client.containers.get(ensured.container_id)
        code, _ = ctr.exec_run(
            f"sh -c 'mkdir -p {CODE_SERVER_CONFIG_CONTAINER_PATH} {CODE_SERVER_DATA_CONTAINER_PATH} "
            f"&& echo cfg > {CODE_SERVER_CONFIG_CONTAINER_PATH}/marker.conf "
            f"&& echo data > {CODE_SERVER_DATA_CONTAINER_PATH}/marker.txt'",
            demux=False,
        )
        assert code == 0

        isolated_runtime.adapter.stop_container(container_id=ensured.container_id)

        with open(os.path.join(cfg_h, "marker.conf"), encoding="utf-8") as f:
            assert f.read().strip() == "cfg"
        with open(os.path.join(data_h, "marker.txt"), encoding="utf-8") as f:
            assert f.read().strip() == "data"
    finally:
        shutil.rmtree(cfg_h, ignore_errors=True)
        shutil.rmtree(data_h, ignore_errors=True)


def test_get_container_netns_ref_returns_real_pid_and_path(
    isolated_runtime: IsolatedRuntimeContext,
) -> None:
    ensured = _ensure(isolated_runtime)
    isolated_runtime.adapter.start_container(container_id=ensured.container_id)

    ref = isolated_runtime.adapter.get_container_netns_ref(container_id=ensured.container_id)

    assert ref.container_id
    assert ref.pid is not None and ref.pid > 0
    assert ref.netns_ref == f"/proc/{ref.pid}/ns/net"


def test_get_container_netns_ref_fails_when_not_running(
    isolated_runtime: IsolatedRuntimeContext,
) -> None:
    ensured = _ensure(isolated_runtime)

    with pytest.raises(NetnsRefError, match="no host PID"):
        isolated_runtime.adapter.get_container_netns_ref(container_id=ensured.container_id)
