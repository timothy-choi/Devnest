"""Snapshot export/import across execution hosts (Phase 3b Step 10).

The control-plane worker may not see workspace bind-mount paths that exist only on a remote
execution node. When a Docker engine client is available (local or ``ssh://``), we stream
``tar`` from inside the running workspace container. When the project tree exists only on a
remote host over SSH, we stream ``tar`` over SSH for STOPPED-style host paths.
"""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
from pathlib import Path

import docker
import docker.errors

from app.libs.runtime.models import WORKSPACE_PROJECT_CONTAINER_PATH
from app.services.node_execution_service.ssh_command_runner import SshRemoteCommandRunner

logger = logging.getLogger(__name__)


def export_running_workspace_tar_from_container(
    *,
    docker_client: docker.DockerClient,
    container_id: str,
    dest: Path,
) -> tuple[bool, list[str]]:
    """Stream ``tar.gz`` of ``WORKSPACE_PROJECT_CONTAINER_PATH`` from a running container to ``dest``."""
    api = docker_client.api
    try:
        api.inspect_container(container_id)
    except docker.errors.NotFound:
        return False, ["snapshot:export:container_not_found"]
    except docker.errors.APIError as e:
        return False, [f"snapshot:export:inspect_failed:{e}"]

    cmd = ["tar", "-czf", "-", "-C", WORKSPACE_PROJECT_CONTAINER_PATH, "."]
    try:
        ex = api.exec_create(container_id, cmd, stdout=True, stderr=True)
        eid = ex["Id"]
        stream = api.exec_start(eid, stream=True, demux=True)
        stderr_acc = bytearray()
        with dest.open("wb") as outfile:
            for chunk in stream:
                if not chunk:
                    continue
                stdout_b, stderr_b = chunk
                if stdout_b:
                    outfile.write(stdout_b)
                if stderr_b:
                    stderr_acc.extend(stderr_b)
        insp = api.exec_inspect(eid)
        code = insp.get("ExitCode")
        if code != 0:
            tail = bytes(stderr_acc).decode(errors="replace")[:2000]
            return False, [f"snapshot:export:tar_nonzero_exit:{code}", tail]
    except OSError as e:
        try:
            if dest.is_file():
                dest.unlink()
        except OSError:
            pass
        return False, [f"snapshot:export:write_failed:{e}"]
    except docker.errors.APIError as e:
        return False, [f"snapshot:export:docker_exec_failed:{e}"]

    return True, []


def export_host_tree_tar_via_ssh(
    *,
    ssh_runner: SshRemoteCommandRunner,
    host_project_root: str,
    dest: Path,
) -> tuple[bool, list[str]]:
    """Stream ``tar.gz`` of ``host_project_root`` on the SSH execution host to local ``dest``."""
    root = (host_project_root or "").strip()
    if not root.startswith("/"):
        return False, ["snapshot:export:remote_host_path_not_absolute"]
    inner = f"tar -czf - -C {shlex.quote(root)} ."
    try:
        proc = ssh_runner.popen_for_remote_command(["sh", "-c", inner], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        return False, [f"snapshot:export:ssh_popen_failed:{e}"]
    assert proc.stdout is not None
    try:
        with dest.open("wb") as outfile:
            shutil.copyfileobj(proc.stdout, outfile)
    except OSError as e:
        try:
            if dest.is_file():
                dest.unlink()
        except OSError:
            pass
        proc.kill()
        return False, [f"snapshot:export:write_failed:{e}"]
    stderr_b = proc.stderr.read() if proc.stderr is not None else b""
    rc = proc.wait()
    if rc != 0:
        try:
            if dest.is_file():
                dest.unlink()
        except OSError:
            pass
        tail = (stderr_b or b"").decode(errors="replace")[:2000]
        return False, [f"snapshot:export:remote_tar_exit:{rc}", tail]
    return True, []


def import_archive_to_host_via_ssh(
    *,
    ssh_runner: SshRemoteCommandRunner,
    host_project_root: str,
    archive_path: Path,
) -> tuple[bool, list[str]]:
    """Extract ``archive_path`` (local worker) into ``host_project_root`` on the SSH host."""
    root = (host_project_root or "").strip()
    if not root.startswith("/"):
        return False, ["snapshot:import:remote_host_path_not_absolute"]
    inner = f"mkdir -p {shlex.quote(root)} && tar -xzf - -C {shlex.quote(root)}"
    try:
        proc = ssh_runner.popen_for_remote_command(
            ["sh", "-c", inner],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as e:
        return False, [f"snapshot:import:ssh_popen_failed:{e}"]
    assert proc.stdin is not None
    try:
        with archive_path.open("rb") as inf:
            shutil.copyfileobj(inf, proc.stdin)
        proc.stdin.close()
    except OSError as e:
        proc.kill()
        return False, [f"snapshot:import:stdin_write_failed:{e}"]
    stderr_b = proc.stderr.read() if proc.stderr is not None else b""
    rc = proc.wait()
    if rc != 0:
        tail = (stderr_b or b"").decode(errors="replace")[:2000]
        return False, [f"snapshot:import:remote_tar_exit:{rc}", tail]
    return True, []
