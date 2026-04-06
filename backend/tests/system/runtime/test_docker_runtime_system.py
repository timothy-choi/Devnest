"""System tests: ``DockerRuntimeAdapter`` against a real Docker engine (no HTTP, no topology)."""

from __future__ import annotations

import pytest

from app.libs.runtime.errors import NetnsRefError

from ..isolated_context import IsolatedRuntimeContext


pytestmark = pytest.mark.system


def _ensure(isolated_runtime: IsolatedRuntimeContext):
    return isolated_runtime.adapter.ensure_container(
        name=isolated_runtime.name,
        image=isolated_runtime.image,
        workspace_host_path=isolated_runtime.workspace_host_path,
        ports=((isolated_runtime.host_port, 8080),),
        labels={"devnest.system_test": "1"},
    )


def test_ensure_container_creates_container(isolated_runtime: IsolatedRuntimeContext) -> None:
    r = _ensure(isolated_runtime)

    assert r.exists is True
    assert r.created_new is True
    assert r.container_id
    assert r.resolved_ports


def test_start_container_starts_container(isolated_runtime: IsolatedRuntimeContext) -> None:
    ensured = _ensure(isolated_runtime)
    started = isolated_runtime.adapter.start_container(container_id=ensured.container_id)

    assert started.success is True
    assert started.container_state == "running"


def test_inspect_container_returns_real_data(isolated_runtime: IsolatedRuntimeContext) -> None:
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


def test_get_container_netns_ref_returns_pid(isolated_runtime: IsolatedRuntimeContext) -> None:
    ensured = _ensure(isolated_runtime)
    isolated_runtime.adapter.start_container(container_id=ensured.container_id)

    ref = isolated_runtime.adapter.get_container_netns_ref(container_id=ensured.container_id)

    assert ref.container_id
    assert ref.pid is not None and ref.pid > 0
    assert ref.netns_ref == f"/proc/{ref.pid}/ns/net"


def test_get_container_netns_ref_fails_when_not_running(isolated_runtime: IsolatedRuntimeContext) -> None:
    ensured = _ensure(isolated_runtime)

    with pytest.raises(NetnsRefError, match="no host PID"):
        isolated_runtime.adapter.get_container_netns_ref(container_id=ensured.container_id)
