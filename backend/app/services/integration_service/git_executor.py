"""Execute git operations inside a running workspace container.

Architecture decision
---------------------
Git commands (clone, pull, push) are run **inside the workspace container** via the
same ``NodeExecutionBundle`` used by the orchestrator:

- **local_docker / ssh_docker**: ``docker exec`` the container and capture stdout/stderr.
  - ``local_docker`` uses the Docker SDK's ``container.exec_run()`` directly.
  - ``ssh_docker`` uses ``SshRemoteCommandRunner`` to wrap ``docker exec`` as a
    remote shell command.
- **ssm_docker**: ``SsmRemoteCommandRunner`` wraps ``docker exec`` via AWS SSM
  Run Command.

This keeps credentials (provider tokens) server-side; only the git operation
result is returned to the API caller.  The token is injected via environment
variable ``GITHUB_TOKEN`` rather than embedded in the URL to avoid accidental
log leakage — git is configured to use a credential helper that reads it.

Returned ``GitResult`` captures exit_code + combined stdout/stderr for API
responses and audit logs.  The token is never included in ``GitResult.output``.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

_logger = logging.getLogger(__name__)


@dataclass
class GitResult:
    exit_code: int
    output: str  # combined stdout + stderr, token-free

    @property
    def success(self) -> bool:
        return self.exit_code == 0


class GitExecutionError(Exception):
    """Raised when the git command cannot be dispatched (not a git failure)."""


def _mask_token(text: str, token: str | None) -> str:
    """Remove the provider token from output to avoid accidental logging."""
    if token and token in text:
        return text.replace(token, "***")
    return text


def run_git_in_container(
    bundle: "NodeExecutionBundle",  # noqa: F821 — forward ref; avoid circular import
    container_id: str,
    git_args: list[str],
    *,
    workdir: str = "/workspace",
    provider_token: str | None = None,
    timeout_seconds: int = 120,
) -> GitResult:
    """Run ``git <git_args>`` inside the workspace container.

    Args:
        bundle: Execution bundle from :func:`~app.services.node_execution_service.factory.resolve_node_execution_bundle`.
        container_id: Docker container ID/name.
        git_args: Arguments passed to ``git`` (e.g. ``["clone", url, dir]``).
        workdir: Working directory inside the container.
        provider_token: If supplied, injected as ``GITHUB_TOKEN`` env var.
            Git is pre-configured with ``credential.helper`` to read it.
        timeout_seconds: Maximum time for the command.

    Returns:
        :class:`GitResult` with exit code and combined output.

    Raises:
        :class:`GitExecutionError` if the command cannot be dispatched.
    """
    # Build git invocation with credential helper when token is present.
    if provider_token:
        # Use git's credential.helper to read GITHUB_TOKEN env var without leaking
        # the token in the command string or process args.
        git_cmd = [
            "git",
            "-c", "credential.helper=",
            "-c",
            "credential.helper=!f(){ echo protocol=https; echo host=github.com;"
            " echo username=oauth2; echo password=$GITHUB_TOKEN; };f",
        ] + git_args
        env_overrides: dict[str, str] = {
            "GITHUB_TOKEN": provider_token,
            "GIT_TERMINAL_PROMPT": "0",
        }
    else:
        git_cmd = ["git"] + git_args
        env_overrides = {"GIT_TERMINAL_PROMPT": "0"}

    # local_docker and ssh_docker both have a docker_client; ssm_docker does not.
    if bundle.docker_client is not None:
        return _run_docker_sdk(bundle, container_id, git_cmd, env_overrides, workdir, provider_token)
    else:
        # ssm_docker: use topology_command_runner to issue ``docker exec`` on the remote instance.
        return _run_via_command_runner(bundle, container_id, git_cmd, env_overrides, workdir, provider_token, timeout_seconds)


def _run_docker_sdk(
    bundle: "NodeExecutionBundle",  # noqa: F821
    container_id: str,
    git_cmd: list[str],
    env: dict[str, str],
    workdir: str,
    token: str | None,
) -> GitResult:
    try:
        docker_client = bundle.docker_client
        container = docker_client.containers.get(container_id)
        exec_result = container.exec_run(
            cmd=git_cmd,
            environment=env,
            workdir=workdir,
            demux=False,
        )
        exit_code: int = int(exec_result.exit_code or 0)
        raw_output: str = (exec_result.output or b"").decode(errors="replace")
        output = _mask_token(raw_output, token)
        _logger.debug("git_exec_docker", extra={"exit_code": exit_code, "cmd_head": git_cmd[0:3]})
        return GitResult(exit_code=exit_code, output=output)
    except Exception as exc:
        raise GitExecutionError(f"docker_exec_failed: {exc}") from exc


def _run_via_command_runner(
    bundle: "NodeExecutionBundle",  # noqa: F821
    container_id: str,
    git_cmd: list[str],
    env: dict[str, str],
    workdir: str,
    token: str | None,
    timeout_seconds: int,
) -> GitResult:
    try:
        runner = bundle.topology_command_runner
        # Build: docker exec -w <workdir> -e K=V ... <container_id> git ...
        docker_args = ["docker", "exec", "-w", workdir]
        for k, v in env.items():
            docker_args += ["-e", f"{k}={v}"]
        docker_args.append(container_id)
        docker_args.extend(git_cmd)

        raw_output = runner.run(docker_args)
        output = _mask_token(raw_output or "", token)
        return GitResult(exit_code=0, output=output)
    except subprocess.CalledProcessError as exc:
        # Preserve the real exit code from the subprocess for accurate reporting.
        exit_code = exc.returncode
        raw = (exc.output or b"").decode(errors="replace") if isinstance(exc.output, bytes) else str(exc.output or "")
        raw = raw or str(exc)
        output = _mask_token(raw, token)
        _logger.warning("git_exec_command_runner_error", extra={"exit_code": exit_code, "error": output[:256]})
        return GitResult(exit_code=exit_code, output=output)
    except Exception as exc:
        raw = str(exc)
        output = _mask_token(raw, token)
        _logger.warning("git_exec_command_runner_error", extra={"exit_code": 1, "error": output[:256]})
        return GitResult(exit_code=1, output=output)
