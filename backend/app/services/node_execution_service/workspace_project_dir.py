"""Ensure workspace project bind-mount directories exist on the execution host."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from app.libs.topology.system.command_runner import CommandRunner

_WORKSPACE_ID_SAFE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")


def default_local_ensure_workspace_project_dir(projects_base: str, workspace_id: str) -> str:
    """Create ``{projects_base}/{workspace_id}`` on this machine; return absolute path string."""
    wid = _validate_workspace_id_for_path(workspace_id)
    base = (projects_base or "").strip()
    if not base:
        base = os.path.join(tempfile.gettempdir(), "devnest-workspaces")
    p = Path(base).expanduser().resolve() / wid
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError(f"cannot create workspace project directory {p}: {e}") from e
    return str(p)


def ssh_remote_ensure_workspace_project_dir(
    runner: CommandRunner,
    projects_base: str,
    workspace_id: str,
) -> str:
    """
    Create ``{projects_base}/{workspace_id}`` on the SSH target using POSIX paths.

    ``projects_base`` must be the path **on the remote Docker host** (same filesystem the daemon
    bind-mounts from).
    """
    wid = _validate_workspace_id_for_path(workspace_id)
    base = (projects_base or "").strip().rstrip("/")
    if not base.startswith("/"):
        raise ValueError("remote workspace_projects_base must be an absolute POSIX path")
    remote_path = f"{base}/{wid}"
    runner.run(["mkdir", "-p", remote_path])
    return remote_path


def _validate_workspace_id_for_path(workspace_id: str) -> str:
    wid = (workspace_id or "").strip()
    if not wid or not _WORKSPACE_ID_SAFE.match(wid):
        raise ValueError(f"unsafe or empty workspace_id for host path: {workspace_id!r}")
    return wid
