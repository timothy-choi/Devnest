"""Guardrails: chaos scripts and RELIABILITY.md stay available (no destructive actions here)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.chaos

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_reliability_doc_exists() -> None:
    doc = REPO_ROOT / "RELIABILITY.md"
    assert doc.is_file(), f"Missing {doc}"
    text = doc.read_text(encoding="utf-8")
    for needle in (
        "Scenario 1",
        "workspace.job.retry_scheduled",
        "autoscaler.scale_up.reason",
        "cleanup_task_",
        "ec2_orphan_janitor",
    ):
        assert needle in text, f"RELIABILITY.md should mention {needle!r}"


def test_chaos_scripts_exist_and_executable() -> None:
    chaos_dir = REPO_ROOT / "scripts" / "chaos"
    assert chaos_dir.is_dir()
    required = [
        "README.md",
        "common.sh",
        "kill_workspace_worker.sh",
        "restart_docker_execution_host.sh",
        "terminate_ec2_execution_node.sh",
        "simulate_ssm_failure.sh",
        "simulate_s3_snapshot_failure.sh",
    ]
    for name in required:
        path = chaos_dir / name
        assert path.is_file(), f"Missing chaos artifact {path}"
        if name.endswith(".sh"):
            mode = path.stat().st_mode
            assert mode & 0o111, f"{path} should be executable (chmod +x)"
