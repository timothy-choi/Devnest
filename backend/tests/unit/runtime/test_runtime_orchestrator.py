"""Unit tests: ``ensure_running_runtime_only`` (no Docker, no DB)."""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from app.libs.runtime.models import (
    ContainerInspectionResult,
    NetnsRefResult,
    RuntimeActionResult,
    RuntimeEnsureResult,
)
from app.libs.runtime.runtime_orchestrator import ensure_running_runtime_only


def test_skip_netns_resolution_omits_get_container_netns_ref() -> None:
    """Orchestrator passes this when ``DEVNEST_TOPOLOGY_SKIP_LINUX_ATTACHMENT`` is set (CI)."""
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
        mounts=(),
    )
    runtime = MagicMock()
    runtime.ensure_container.return_value = ensure
    runtime.start_container.return_value = start
    runtime.inspect_container.return_value = inspect
    runtime.fetch_container_log_tail.return_value = ""
    runtime.get_container_netns_ref.return_value = NetnsRefResult(
        container_id="abc123",
        pid=5000,
        netns_ref="/proc/5000/ns/net",
    )

    out = ensure_running_runtime_only(
        runtime,
        name="w",
        workspace_host_path="/host",
        skip_netns_resolution=True,
    )

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
                project_mount=None,
                workspace_host_path="/host",
                extra_bind_mounts=None,
                existing_container_id=None,
            ),
            call.start_container(container_id="abc123"),
            call.inspect_container(container_id="abc123"),
        ],
        any_order=False,
    )
    runtime.get_container_netns_ref.assert_not_called()
    assert out.pid == 0
    assert out.netns_ref == "/devnest-skip-linux-topology-attachment"
    assert out.container_id == "abc123"


def test_post_start_inspect_requires_host_pid_when_netns_enabled() -> None:
    """If the engine never reports a positive PID, fail before get_container_netns_ref."""
    from app.libs.runtime.errors import ContainerStartError

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
        pid=0,
        ports=((18080, 8080),),
        mounts=(),
    )
    runtime = MagicMock()
    runtime.ensure_container.return_value = ensure
    runtime.start_container.return_value = start
    runtime.inspect_container.return_value = inspect
    runtime.fetch_container_log_tail.return_value = ""

    with pytest.raises(ContainerStartError, match="host PID"):
        ensure_running_runtime_only(runtime, name="w", workspace_host_path="/host", skip_netns_resolution=False)

    runtime.get_container_netns_ref.assert_not_called()
