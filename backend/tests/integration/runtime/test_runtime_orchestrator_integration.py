"""Integration-style tests: orchestrator + adapter contract (no HTTP, no Docker daemon)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from unittest.mock import MagicMock, call

import pytest

from app.libs.runtime.models import (
    ContainerInspectionResult,
    NetnsRefResult,
    RuntimeActionResult,
    RuntimeEnsureResult,
)
from app.libs.runtime.runtime_orchestrator import ensure_running_runtime_only


class _RecordingRuntimeAdapter:
    """Minimal stand-in for ``RuntimeAdapter``; records call order (duck-typed)."""

    def __init__(self) -> None:
        self.call_log: list[str] = []
        self._ensure: RuntimeEnsureResult | None = None
        self._start: RuntimeActionResult | None = None
        self._inspect: ContainerInspectionResult | None = None
        self._netns: NetnsRefResult | None = None

    def set_returns(
        self,
        *,
        ensure: RuntimeEnsureResult,
        start: RuntimeActionResult,
        inspect: ContainerInspectionResult,
        netns: NetnsRefResult,
    ) -> None:
        self._ensure = ensure
        self._start = start
        self._inspect = inspect
        self._netns = netns

    def ensure_container(
        self,
        *,
        name: str,
        image: str | None = None,
        cpu_limit: float | None = None,
        memory_limit_bytes: int | None = None,
        env: Mapping[str, str] | None = None,
        ports: Sequence[tuple[int, int]] | None = None,
        labels: Mapping[str, str] | None = None,
        workspace_host_path: str | None = None,
        existing_container_id: str | None = None,
    ) -> RuntimeEnsureResult:
        self.call_log.append("ensure_container")
        assert self._ensure is not None
        return self._ensure

    def start_container(self, *, container_id: str) -> RuntimeActionResult:
        self.call_log.append("start_container")
        assert self._start is not None
        return self._start

    def inspect_container(self, *, container_id: str) -> ContainerInspectionResult:
        self.call_log.append("inspect_container")
        assert self._inspect is not None
        return self._inspect

    def get_container_netns_ref(self, *, container_id: str) -> NetnsRefResult:
        self.call_log.append("get_container_netns_ref")
        assert self._netns is not None
        return self._netns

    def stop_container(self, *, container_id: str) -> RuntimeActionResult:
        raise NotImplementedError

    def restart_container(self, *, container_id: str) -> RuntimeActionResult:
        raise NotImplementedError

    def delete_container(self, *, container_id: str) -> RuntimeActionResult:
        raise NotImplementedError


@pytest.fixture
def happy_results() -> tuple[RuntimeEnsureResult, RuntimeActionResult, ContainerInspectionResult, NetnsRefResult]:
    ensure = RuntimeEnsureResult(
        container_id="abc123",
        exists=True,
        created_new=True,
        container_state="created",
        resolved_ports=((8080, 8080),),
        node_id="test-node",
    )
    start = RuntimeActionResult(
        container_id="abc123",
        container_state="running",
        success=True,
        message=None,
    )
    inspect = ContainerInspectionResult(
        exists=True,
        container_id="abc123",
        container_state="running",
        pid=5000,
        ports=((18080, 8080),),
        mounts=("/host:/home/coder/project",),
    )
    netns = NetnsRefResult(container_id="abc123", pid=5000, netns_ref="/proc/5000/ns/net")
    return ensure, start, inspect, netns


def test_ensure_running_runtime_only_call_order_and_result(
    happy_results: tuple[RuntimeEnsureResult, RuntimeActionResult, ContainerInspectionResult, NetnsRefResult],
) -> None:
    ensure, start, inspect, netns = happy_results
    runtime = _RecordingRuntimeAdapter()
    runtime.set_returns(ensure=ensure, start=start, inspect=inspect, netns=netns)

    out = ensure_running_runtime_only(
        runtime,
        name="workspace-1",
        workspace_host_path="/data/w1",
        image="img:x",
        env={"A": "b"},
    )

    assert runtime.call_log == [
        "ensure_container",
        "start_container",
        "inspect_container",
        "get_container_netns_ref",
    ]
    assert out.container_id == "abc123"
    assert out.container_state == "running"
    assert out.pid == 5000
    assert out.netns_ref == "/proc/5000/ns/net"
    assert out.ports == ((18080, 8080),)
    assert out.node_id == "test-node"


def test_ensure_running_falls_back_to_resolved_ports_when_inspect_ports_empty(
    happy_results: tuple[RuntimeEnsureResult, RuntimeActionResult, ContainerInspectionResult, NetnsRefResult],
) -> None:
    ensure, start, _, netns = happy_results
    inspect_no_ports = ContainerInspectionResult(
        exists=True,
        container_id="abc123",
        container_state="running",
        pid=5000,
        ports=(),
        mounts=(),
    )
    runtime = _RecordingRuntimeAdapter()
    runtime.set_returns(ensure=ensure, start=start, inspect=inspect_no_ports, netns=netns)

    out = ensure_running_runtime_only(runtime, name="n", workspace_host_path="/w")

    assert out.ports == ((8080, 8080),)


def test_magicmock_strict_call_sequence_matches_documentation(
    happy_results: tuple[RuntimeEnsureResult, RuntimeActionResult, ContainerInspectionResult, NetnsRefResult],
) -> None:
    """``MagicMock`` + ``assert_has_calls`` variant (same ordering contract)."""
    ensure, start, inspect, netns = happy_results
    runtime = MagicMock()
    runtime.ensure_container.return_value = ensure
    runtime.start_container.return_value = start
    runtime.inspect_container.return_value = inspect
    runtime.get_container_netns_ref.return_value = netns

    ensure_running_runtime_only(runtime, name="w", workspace_host_path="/host")

    runtime.assert_has_calls(
        [
            call.ensure_container(
                name="w",
                image=None,
                cpu_limit=None,
                memory_limit_bytes=None,
                env=None,
                ports=None,
                labels=None,
                workspace_host_path="/host",
                existing_container_id=None,
            ),
            call.start_container(container_id="abc123"),
            call.inspect_container(container_id="abc123"),
            call.get_container_netns_ref(container_id="abc123"),
        ],
        any_order=False,
    )


def test_empty_container_id_from_ensure_raises_before_start() -> None:
    from app.libs.runtime.errors import ContainerCreateError

    runtime = _RecordingRuntimeAdapter()
    runtime.set_returns(
        ensure=RuntimeEnsureResult(
            container_id="",
            exists=True,
            created_new=False,
            container_state="running",
            resolved_ports=(),
        ),
        start=RuntimeActionResult(container_id="", container_state="running", success=True),
        inspect=ContainerInspectionResult(
            exists=True,
            container_id="",
            container_state="running",
            pid=1,
            ports=(),
            mounts=(),
        ),
        netns=NetnsRefResult(container_id="", pid=1, netns_ref="/proc/1/ns/net"),
    )

    with pytest.raises(ContainerCreateError, match="empty container_id"):
        ensure_running_runtime_only(runtime, name="n", workspace_host_path="/w")

    assert runtime.call_log == ["ensure_container"]


def test_start_failure_skips_inspect_and_netns() -> None:
    from app.libs.runtime.errors import ContainerStartError

    runtime = _RecordingRuntimeAdapter()
    runtime.set_returns(
        ensure=RuntimeEnsureResult(
            container_id="x",
            exists=True,
            created_new=False,
            container_state="exited",
            resolved_ports=((8080, 8080),),
        ),
        start=RuntimeActionResult(
            container_id="x",
            container_state="dead",
            success=False,
            message="cannot start",
        ),
        inspect=ContainerInspectionResult(
            exists=True,
            container_id="x",
            container_state="dead",
            pid=None,
            ports=(),
            mounts=(),
        ),
        netns=NetnsRefResult(container_id="x", pid=1, netns_ref="/proc/1/ns/net"),
    )

    with pytest.raises(ContainerStartError, match="cannot start"):
        ensure_running_runtime_only(runtime, name="n", workspace_host_path="/w")

    assert runtime.call_log == ["ensure_container", "start_container"]
