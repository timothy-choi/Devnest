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
