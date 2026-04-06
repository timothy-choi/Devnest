"""
System tests: ``DockerRuntimeAdapter`` against a real Docker engine (no mocks, no HTTP API).

Isolation: each test gets a unique container name and temp workspace directory; the
``isolated_runtime`` fixture removes the container and directory in ``finally`` even when
a test fails.

**How to run only these tests** (from ``backend/``)::

    pytest tests/system/runtime/ -v -m system

Or by path (skips collection of unrelated modules)::

    pytest tests/system/runtime/test_docker_runtime_system.py -v

Image: set ``DEVNEST_RUNTIME_SYSTEM_IMAGE`` to override the default ``nginx:alpine``
(see ``tests/system/conftest.py``). The adapter still publishes container port 8080 with
an **ephemeral** host port so nothing binds a fixed host port like 8080.
"""

from __future__ import annotations

import pytest

from app.libs.runtime.errors import NetnsRefError

from ..isolated_context import IsolatedRuntimeContext


pytestmark = pytest.mark.system


def _ensure(isolated_runtime: IsolatedRuntimeContext):
    """Create via adapter; omit ``ports`` so host side is engine-assigned (ephemeral)."""
    return isolated_runtime.adapter.ensure_container(
        name=isolated_runtime.name,
        image=isolated_runtime.image,
        workspace_host_path=isolated_runtime.workspace_host_path,
        labels={"devnest.system_test": "runtime"},
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
    assert pair[1] == 8080
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


def test_delete_container_removes_container(isolated_runtime: IsolatedRuntimeContext) -> None:
    ensured = _ensure(isolated_runtime)
    isolated_runtime.adapter.start_container(container_id=ensured.container_id)

    deleted = isolated_runtime.adapter.delete_container(container_id=ensured.container_id)

    assert deleted.success is True
    assert deleted.container_state == "missing"

    after = isolated_runtime.adapter.inspect_container(container_id=ensured.container_id)
    assert after.exists is False
    assert after.container_state == "missing"


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
