"""Validate compose interpolation and service graph via Docker CLI."""

import shutil
import subprocess
from pathlib import Path


def test_docker_compose_config_succeeds(gateway_root: Path) -> None:
    if not shutil.which("docker"):
        import pytest

        pytest.skip("docker not on PATH")
    result = subprocess.run(
        ["docker", "compose", "config"],
        cwd=gateway_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
