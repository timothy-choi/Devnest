"""Ensure workspace project bind-mount directories exist on the execution host."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

from app.libs.topology.system.command_runner import CommandRunner

logger = logging.getLogger(__name__)

_WORKSPACE_ID_SAFE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")


def workspace_container_uid_gid() -> tuple[int, int]:
    """UID/GID of the non-root user inside the workspace image (bind mounts must match on the host)."""
    uid_raw = os.environ.get("DEVNEST_WORKSPACE_CONTAINER_UID", "1000").strip()
    gid_raw = os.environ.get("DEVNEST_WORKSPACE_CONTAINER_GID", "1000").strip()
    try:
        return int(uid_raw), int(gid_raw)
    except ValueError:
        return 1000, 1000


def ensure_host_path_owned_by_workspace_user(path: str, *, strict: bool = False) -> None:
    """
    Recursively ``chown`` a host path tree to the workspace container user (default 1000:1000).

    Orchestrator and API processes often run as root and create directories with root ownership.
    Those paths are bind-mounted into the container where ``code-server`` runs as ``coder``; without
    a matching UID/GID on the host, writes (e.g. ``config.yaml``) fail with EACCES.

    When ``strict`` is True, ``OSError`` from ``chown`` is re-raised after logging so callers do not
    bind-mount root-owned trees that will crash code-server and surface as misleading netns errors.
    """
    root = Path(path).resolve()
    if not root.exists():
        return
    uid, gid = workspace_container_uid_gid()
    try:
        if root.is_file():
            os.chown(root, uid, gid, follow_symlinks=False)
            return
        for dirpath, dirnames, filenames in os.walk(root, topdown=False):
            for name in filenames:
                os.chown(os.path.join(dirpath, name), uid, gid, follow_symlinks=False)
            for name in dirnames:
                os.chown(os.path.join(dirpath, name), uid, gid, follow_symlinks=False)
        os.chown(root, uid, gid, follow_symlinks=False)
    except OSError as e:
        logger.warning(
            "workspace_host_chown_failed",
            extra={"path": str(root), "uid": uid, "gid": gid, "error": str(e), "strict": strict},
        )
        if strict:
            raise


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
    ensure_host_path_owned_by_workspace_user(str(p))
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


# Same as :func:`ssh_remote_ensure_workspace_project_dir` — used when ``runner`` is SSM-backed.
remote_shell_ensure_workspace_project_dir = ssh_remote_ensure_workspace_project_dir


def _validate_workspace_id_for_path(workspace_id: str) -> str:
    wid = (workspace_id or "").strip()
    if not wid or not _WORKSPACE_ID_SAFE.match(wid):
        raise ValueError(f"unsafe or empty workspace_id for host path: {workspace_id!r}")
    return wid
