"""Unit tests for git executor — validates command building and output masking."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_bundle(mode: str = "local_docker") -> MagicMock:
    bundle = MagicMock()
    if mode == "local_docker":
        bundle.docker_client = MagicMock()
    else:
        bundle.docker_client = None
    return bundle


def test_local_docker_success():
    """Happy path: docker SDK exec_run returns exit_code 0."""
    from app.services.integration_service.git_executor import GitResult, run_git_in_container

    exec_result = MagicMock()
    exec_result.exit_code = 0
    exec_result.output = b"Cloning into 'project'...\n"

    container = MagicMock()
    container.exec_run.return_value = exec_result

    bundle = _make_bundle("local_docker")
    bundle.docker_client.containers.get.return_value = container

    result = run_git_in_container(bundle, "container123", ["clone", "https://github.com/a/b.git", "/workspace"])
    assert result.success
    assert result.exit_code == 0
    assert "Cloning" in result.output


def test_local_docker_failure_exit_code():
    """Non-zero exit code is surfaced correctly."""
    from app.services.integration_service.git_executor import run_git_in_container

    exec_result = MagicMock()
    exec_result.exit_code = 128
    exec_result.output = b"fatal: repository not found\n"

    container = MagicMock()
    container.exec_run.return_value = exec_result

    bundle = _make_bundle("local_docker")
    bundle.docker_client.containers.get.return_value = container

    result = run_git_in_container(bundle, "cid", ["clone", "url", "dir"])
    assert not result.success
    assert result.exit_code == 128
    assert "repository not found" in result.output


def test_token_is_masked_in_output():
    """Provider token must not appear in GitResult.output."""
    from app.services.integration_service.git_executor import run_git_in_container

    token = "ghp_supersecrettoken"
    exec_result = MagicMock()
    exec_result.exit_code = 0
    exec_result.output = f"Cloning https://oauth2:{token}@github.com/a/b.git\n".encode()

    container = MagicMock()
    container.exec_run.return_value = exec_result

    bundle = _make_bundle("local_docker")
    bundle.docker_client.containers.get.return_value = container

    result = run_git_in_container(bundle, "cid", ["clone", "url", "dir"], provider_token=token)
    assert token not in result.output
    assert "***" in result.output


def test_ssm_docker_uses_topology_command_runner():
    """ssm_docker mode (no docker_client) delegates to topology_command_runner with docker exec prefix."""
    from app.services.integration_service.git_executor import run_git_in_container

    # ssm_docker: docker_client is None
    bundle = _make_bundle("ssm_docker")
    bundle.topology_command_runner.run.return_value = "Already up to date."

    result = run_git_in_container(bundle, "container_ssm", ["pull", "origin", "main"])
    assert result.success
    bundle.topology_command_runner.run.assert_called_once()
    call_args = bundle.topology_command_runner.run.call_args[0][0]
    assert call_args[0] == "docker"
    assert "exec" in call_args


def test_docker_sdk_exception_raises_git_execution_error():
    """Docker SDK errors are wrapped in GitExecutionError."""
    from app.services.integration_service.git_executor import GitExecutionError, run_git_in_container

    bundle = _make_bundle("local_docker")
    bundle.docker_client.containers.get.side_effect = Exception("container not found")

    with pytest.raises(GitExecutionError, match="docker_exec_failed"):
        run_git_in_container(bundle, "missing_cid", ["status"])
