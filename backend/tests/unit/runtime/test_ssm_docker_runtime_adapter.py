"""Unit tests: :class:`SsmDockerRuntimeAdapter` inspect path (mocked runner)."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.libs.runtime.ssm_docker_runtime import SsmDockerRuntimeAdapter


def test_inspect_container_parses_docker_inspect_json() -> None:
    sample = """[{
        "Id": "abc123",
        "State": {"Status": "running"},
        "Config": {"Labels": {}},
        "NetworkSettings": {"Ports": {}}
    }]"""
    runner = MagicMock()
    runner.run.return_value = sample
    rt = SsmDockerRuntimeAdapter(runner)
    ins = rt.inspect_container(container_id="abc123")
    assert ins.exists is True
    assert ins.container_state == "running"
    runner.run.assert_called_once()
    assert runner.run.call_args[0][0][:2] == ["docker", "inspect"]


def test_inspect_container_missing_returns_not_exists() -> None:
    runner = MagicMock()
    runner.run.side_effect = RuntimeError("docker: no such object")
    rt = SsmDockerRuntimeAdapter(runner)
    ins = rt.inspect_container(container_id="nope")
    assert ins.exists is False


def test_ensure_container_passes_limits_and_security_argv() -> None:
    from app.libs.runtime.workspace_container_policy import WorkspaceContainerSecuritySpec

    inspect_tail = """[{
        "Id": "abc123",
        "State": {"Status": "created", "Pid": 0},
        "Config": {"Labels": {}},
        "NetworkSettings": {"Ports": {}},
        "Mounts": [
            {"Type": "bind", "Source": "/host/ws", "Destination": "/home/coder/project", "RW": true}
        ]
    }]"""

    runner = MagicMock()
    runner.run.side_effect = [
        RuntimeError("no such object"),
        "",
        "abc123\n",
        inspect_tail,
    ]
    rt = SsmDockerRuntimeAdapter(runner)

    sec = WorkspaceContainerSecuritySpec(
        security_opt=("no-new-privileges:true",),
        cap_drop=("NET_RAW",),
        read_only_rootfs=True,
    )
    rt.ensure_container(
        name="ws-x",
        workspace_host_path="/host/ws",
        cpu_limit=0.25,
        memory_limit_bytes=128 * 1024 * 1024,
        pids_limit=99,
        security_spec=sec,
    )

    create_argv = runner.run.call_args_list[2][0][0]
    assert create_argv[0:3] == ["docker", "create", "--name"]
    assert "--cpus" in create_argv
    assert "--memory" in create_argv
    assert "--pids-limit" in create_argv
    assert "--security-opt" in create_argv
    assert "--cap-drop" in create_argv
    assert "--read-only" in create_argv
    assert any(a == "--tmpfs" for a in create_argv)
