"""Unit tests for remote EC2/SSM snapshot export command generation."""

from __future__ import annotations

from app.services.orchestrator_service.snapshot_filesystem import (
    build_ssm_docker_snapshot_export_to_s3_script,
    export_running_workspace_tar_to_s3_via_ssm,
)


def test_build_ssm_docker_snapshot_export_to_s3_script() -> None:
    script = build_ssm_docker_snapshot_export_to_s3_script(
        workspace_id=81,
        snapshot_id=123,
        container_id="abc123",
        s3_uri="s3://devnest-bucket/devnest-snapshots/ws-81/snapshot-123.tar.gz",
    )

    assert "docker exec abc123 tar czf - -C /home/coder/project . > \"$tmp\"" in script
    assert "tmp=/tmp/devnest-ws-81.tar.gz" in script
    assert "aws s3 cp \"$tmp\" s3://devnest-bucket/devnest-snapshots/ws-81/snapshot-123.tar.gz" in script
    assert "rm -f \"$tmp\"" in script
    assert "devnest_snapshot_bytes=" in script


def test_export_running_workspace_tar_to_s3_via_ssm_runs_generated_script() -> None:
    class FakeRunner:
        def __init__(self) -> None:
            self.commands: list[list[str]] = []

        def run(self, cmd: list[str]) -> str:
            self.commands.append(cmd)
            return "devnest_snapshot_bytes=42\n"

    runner = FakeRunner()

    ok, size_bytes, issues = export_running_workspace_tar_to_s3_via_ssm(
        ssm_runner=runner,  # type: ignore[arg-type]
        workspace_id=81,
        snapshot_id=123,
        container_id="abc123",
        s3_uri="s3://devnest-bucket/devnest-snapshots/ws-81/snapshot-123.tar.gz",
    )

    assert ok is True
    assert size_bytes == 42
    assert issues == []
    assert runner.commands
    assert runner.commands[0][:2] == ["sh", "-lc"]
    assert "docker exec abc123 tar czf -" in runner.commands[0][2]
