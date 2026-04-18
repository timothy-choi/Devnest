"""Ensure workspace project bind-mount directories exist on the execution host."""

from __future__ import annotations

import errno
import logging
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from app.libs.topology.system.command_runner import CommandRunner

logger = logging.getLogger(__name__)

_WORKSPACE_ID_SAFE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_WORKSPACE_STORAGE_KEY_SAFE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")


def workspace_container_uid_gid() -> tuple[int, int]:
    """UID/GID of the non-root user inside the workspace image (bind mounts must match on the host)."""
    uid_raw = os.environ.get("DEVNEST_WORKSPACE_CONTAINER_UID", "1000").strip()
    gid_raw = os.environ.get("DEVNEST_WORKSPACE_CONTAINER_GID", "1000").strip()
    try:
        return int(uid_raw), int(gid_raw)
    except ValueError:
        return 1000, 1000


def stat_uid_gid(path: str) -> tuple[int, int]:
    st = os.stat(path, follow_symlinks=False)
    return int(st.st_uid), int(st.st_gid)


def stat_mode_octal(path: str) -> str:
    """POSIX permission bits (e.g. ``0o755``) for logging."""
    st = os.stat(path, follow_symlinks=False)
    return oct(stat.S_IMODE(st.st_mode))


def finalize_workspace_host_project_tree_ownership(project_root: str, *, strict: bool) -> None:
    """Recursively chown the entire workspace bind tree to the container runtime UID/GID.

    Call after all ``mkdir``/seed steps under ``project_root`` (including ``code-server/``) and
    again immediately before ``docker create`` so root-created files cannot remain on the host.
    """
    root = os.path.realpath(os.path.expanduser(str(project_root or "").strip()))
    if not root:
        return
    if not os.path.lexists(root):
        if strict:
            raise OSError(errno.ENOENT, f"workspace project root missing for final chown: {root!r}")
        return
    chown_tree_for_workspace_runtime(root, strict=strict)


def log_workspace_config_bind_host_before_docker_start(workspace_id: str, config_host_path: str) -> None:
    """Log host ownership for the code-server config bind source immediately before ``docker create``."""
    wid = (workspace_id or "").strip() or "unknown"
    cfg = os.path.realpath(os.path.expanduser(str(config_host_path or "").strip()))
    want_uid, want_gid = workspace_container_uid_gid()
    if not cfg or not os.path.lexists(cfg):
        logger.info(
            "workspace_config_bind_host_before_docker_skip",
            extra={
                "workspace_id": wid,
                "host_path": cfg or config_host_path,
                "target_uid": want_uid,
                "target_gid": want_gid,
                "exists": False,
            },
        )
        return
    su, sg = stat_uid_gid(cfg)
    extra: dict[str, object] = {
        "workspace_id": wid,
        "host_path": cfg,
        "stat_uid": su,
        "stat_gid": sg,
        "target_uid": want_uid,
        "target_gid": want_gid,
        "mode_oct": stat_mode_octal(cfg),
        "ownership_matches_target": bool(su == want_uid and sg == want_gid),
    }
    yaml_path = os.path.join(cfg, "config.yaml")
    if os.path.isfile(yaml_path):
        yu, yg = stat_uid_gid(yaml_path)
        extra["config_yaml_stat_uid"] = yu
        extra["config_yaml_stat_gid"] = yg
        extra["config_yaml_mode_oct"] = stat_mode_octal(yaml_path)
        extra["config_yaml_ownership_matches_target"] = bool(yu == want_uid and yg == want_gid)
    logger.info("workspace_config_bind_host_ownership_before_docker_start", extra=extra)


def log_workspace_host_bind_mount_ownership(workspace_id: str, project_root: str) -> None:
    """Emit one log line per key path with uid/gid/mode (post-chown diagnostics)."""
    wid = (workspace_id or "").strip() or "unknown"
    root = os.path.realpath(os.path.expanduser(str(project_root or "").strip()))
    paths: list[tuple[str, str]] = [
        ("workspace_root", root),
        ("code_server", os.path.join(root, "code-server")),
        ("code_server_config", os.path.join(root, "code-server", "config")),
        ("code_server_data", os.path.join(root, "code-server", "data")),
    ]
    for role, path in paths:
        if not path or not os.path.lexists(path):
            logger.info(
                "workspace_host_bind_mount_ownership_skip",
                extra={"workspace_id": wid, "role": role, "host_path": path, "exists": False},
            )
            continue
        su, sg = stat_uid_gid(path)
        logger.info(
            "workspace_host_bind_mount_ownership_final",
            extra={
                "workspace_id": wid,
                "role": role,
                "host_path": path,
                "stat_uid": su,
                "stat_gid": sg,
                "mode_oct": stat_mode_octal(path),
                "is_file": os.path.isfile(path),
            },
        )


def chown_tree_for_workspace_runtime(path: str, *, strict: bool) -> None:
    """
    Recursively set ownership to :func:`workspace_container_uid_gid`.

    On Linux as root, prefer ``chown -R`` (coreutils) so the same code path matches manual EC2 fixes
    and handles odd trees more reliably than a pure-Python walk alone.
    """
    root = os.path.realpath(os.path.expanduser(str(path)))
    if not os.path.lexists(root):
        if strict:
            raise OSError(errno.ENOENT, f"workspace host path missing for chown: {root!r}")
        return
    uid, gid = workspace_container_uid_gid()
    try:
        euid = os.geteuid()
    except AttributeError:
        euid = -1
    if euid == 0 and shutil.which("chown"):
        try:
            cp = subprocess.run(
                ["chown", "-R", f"{uid}:{gid}", root],
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
            logger.info(
                "workspace_host_chown_shell_ok",
                extra={
                    "path": root,
                    "uid": uid,
                    "gid": gid,
                    "stderr": (cp.stderr or "").strip()[:500],
                },
            )
            return
        except subprocess.CalledProcessError as e:
            err = (e.stderr or e.stdout or "").strip()
            logger.warning(
                "workspace_host_chown_shell_failed",
                extra={"path": root, "uid": uid, "gid": gid, "error": str(e), "stderr": err[:500]},
            )
            if strict:
                raise OSError(
                    errno.EACCES,
                    f"chown -R {uid}:{gid} failed for {root!r}: {e}; stderr={err!r}",
                ) from e
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("workspace_host_chown_shell_failed", extra={"path": root, "error": str(e)})
            if strict:
                raise OSError(errno.EACCES, f"chown -R failed for {root!r}: {e}") from e
    ensure_host_path_owned_by_workspace_user(root, strict=strict)


def verify_workspace_runtime_owns_path(path: str) -> None:
    """
    Ensure ``path`` is owned by :func:`workspace_container_uid_gid` (post-``chown`` check).

    Raises:
        OSError: with errno ``errno.EACCES`` when ownership is wrong (caller maps to bring-up error).
    """
    want_uid, want_gid = workspace_container_uid_gid()
    got_uid, got_gid = stat_uid_gid(path)
    if got_uid != want_uid or got_gid != want_gid:
        raise OSError(
            errno.EACCES,
            f"workspace host bind-mount path not owned by runtime user "
            f"(expected uid={want_uid} gid={want_gid}, found uid={got_uid} gid={got_gid}): {path!r}",
        )


def verify_workspace_runtime_can_write_dir(path: str) -> None:
    """
    Confirm ``path`` is writable as the workspace runtime user (not as root; root can bypass DAC).

    Uses ``setpriv`` (preferred) or ``runuser`` when the current process is root so the check runs
    under the target UID/GID without requiring a matching ``/etc/passwd`` row (workspace ``coder``
    may not exist in the control-plane image). Call after :func:`verify_workspace_runtime_owns_path`.
    """
    want_uid, want_gid = workspace_container_uid_gid()
    try:
        euid = os.geteuid()
    except AttributeError:
        return
    if euid != 0:
        return
    quoted = shlex.quote(path)
    setpriv = shutil.which("setpriv")
    if setpriv:
        cmd = [
            setpriv,
            f"--reuid={want_uid}",
            f"--regid={want_gid}",
            "--clear-groups",
            "sh",
            "-c",
            f"test -w {quoted} && test -x {quoted}",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            raise OSError(
                errno.EACCES,
                f"workspace host bind-mount path not writable by runtime user uid={want_uid} "
                f"gid={want_gid} (setpriv check failed for {path!r}): {err}",
            )
        return
    runuser = shutil.which("runuser")
    if not runuser:
        return
    cmd = [
        runuser,
        "-u",
        f"#{want_uid}",
        "-g",
        f"#{want_gid}",
        "--",
        "sh",
        "-c",
        f"test -w {quoted} && test -x {quoted}",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        raise OSError(
            errno.EACCES,
            f"workspace host bind-mount path not writable by runtime user uid={want_uid} "
            f"gid={want_gid} (runuser check failed for {path!r}): {err}",
        )


def verify_workspace_runtime_can_read_write_file(path: str) -> None:
    """
    Confirm ``path`` is readable and writable as the workspace runtime user.

    This catches the common failure mode where a config file is re-created by the control plane as
    ``root:root`` after the parent directory has already been chowned correctly.
    """
    want_uid, want_gid = workspace_container_uid_gid()
    try:
        euid = os.geteuid()
    except AttributeError:
        return
    if euid != 0:
        return
    quoted = shlex.quote(path)
    setpriv = shutil.which("setpriv")
    if setpriv:
        cmd = [
            setpriv,
            f"--reuid={want_uid}",
            f"--regid={want_gid}",
            "--clear-groups",
            "sh",
            "-c",
            f"test -r {quoted} && test -w {quoted}",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            raise OSError(
                errno.EACCES,
                f"workspace host file not readable/writable by runtime user uid={want_uid} "
                f"gid={want_gid} (setpriv check failed for {path!r}): {err}",
            )
        return
    runuser = shutil.which("runuser")
    if not runuser:
        return
    cmd = [
        runuser,
        "-u",
        f"#{want_uid}",
        "-g",
        f"#{want_gid}",
        "--",
        "sh",
        "-c",
        f"test -r {quoted} && test -w {quoted}",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        raise OSError(
            errno.EACCES,
            f"workspace host file not readable/writable by runtime user uid={want_uid} "
            f"gid={want_gid} (runuser check failed for {path!r}): {err}",
        )


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


def workspace_project_dir_name(workspace_id: str, project_storage_key: str | None = None) -> str:
    """Stable host directory name for one workspace record.

    Legacy rows without ``project_storage_key`` keep using ``workspace_id`` directly so existing
    workspaces continue to see their current bind-mounted files after upgrade. New rows with a
    persisted key use ``{workspace_id}-{project_storage_key}`` so a recycled numeric id cannot
    accidentally reuse another workspace's project tree.
    """
    wid = _validate_workspace_id_for_path(workspace_id)
    key = _validate_workspace_storage_key_for_path(project_storage_key)
    if not key:
        return wid
    return f"{wid}-{key}"


def default_local_ensure_workspace_project_dir(
    projects_base: str,
    workspace_id: str,
    project_storage_key: str | None = None,
) -> str:
    """Create the isolated workspace project directory on this machine; return absolute path."""
    wid = _validate_workspace_id_for_path(workspace_id)
    storage_key = _validate_workspace_storage_key_for_path(project_storage_key)
    base = (projects_base or "").strip()
    if not base:
        base = os.path.join(tempfile.gettempdir(), "devnest-workspaces")
    dir_name = workspace_project_dir_name(wid, storage_key)
    p = Path(os.path.realpath(os.path.expanduser(str(Path(base) / dir_name))))
    existed_before = p.exists()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError(f"cannot create workspace project directory {p}: {e}") from e
    p_str = str(p)
    uid, gid = workspace_container_uid_gid()
    try:
        _strict_chown = os.geteuid() == 0  # type: ignore[attr-defined]
    except AttributeError:
        _strict_chown = False
    chown_tree_for_workspace_runtime(p_str, strict=_strict_chown)
    verify_workspace_runtime_owns_path(p_str)
    verify_workspace_runtime_can_write_dir(p_str)
    logger.info(
        "workspace_project_host_dir_ready",
        extra={
            "host_path": p_str,
            "workspace_id": wid,
            "project_storage_key": storage_key,
            "host_dir_name": dir_name,
            "path_mode": "legacy_workspace_id" if not storage_key else "workspace_id_plus_storage_key",
            "directory_preexisted": existed_before,
            "uid": uid,
            "gid": gid,
            "chown_strict": _strict_chown,
            "stat_uid": stat_uid_gid(p_str)[0],
            "stat_gid": stat_uid_gid(p_str)[1],
            "mode_oct": stat_mode_octal(p_str),
            "pre_start_writability_ok": True,
        },
    )
    return p_str


def prune_orphaned_workspace_project_dirs(
    projects_base: str,
    live_workspace_refs: list[tuple[str, str | None]],
) -> list[str]:
    """
    Remove workspace project directories under ``projects_base`` that are not referenced by current DB rows.

    This is intentionally conservative: only direct child directories are considered, and a child is
    kept when its name matches the derived host dir name for any live workspace reference.
    """
    base = (projects_base or "").strip()
    if not base:
        return []
    root = Path(os.path.realpath(os.path.expanduser(base)))
    if not root.exists() or not root.is_dir():
        return []
    live_dir_names = {
        workspace_project_dir_name(str(workspace_id), project_storage_key)
        for workspace_id, project_storage_key in live_workspace_refs
        if str(workspace_id).strip()
    }
    removed: list[str] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if child.name in live_dir_names:
            continue
        shutil.rmtree(child, ignore_errors=False)
        removed.append(str(child))
        logger.info(
            "workspace_project_host_dir_pruned",
            extra={
                "host_path": str(child),
                "host_dir_name": child.name,
                "prune_reason": "orphaned_after_startup_db_scan",
            },
        )
    logger.info(
        "workspace_project_host_dir_prune_complete",
        extra={
            "projects_base": str(root),
            "live_workspace_count": len(live_workspace_refs),
            "removed_count": len(removed),
            "cleanup_occurred": bool(removed),
        },
    )
    return removed


def ssh_remote_ensure_workspace_project_dir(
    runner: CommandRunner,
    projects_base: str,
    workspace_id: str,
    project_storage_key: str | None = None,
) -> str:
    """
    Create the isolated workspace project directory on the SSH target using POSIX paths.

    ``projects_base`` must be the path **on the remote Docker host** (same filesystem the daemon
    bind-mounts from).
    """
    wid = _validate_workspace_id_for_path(workspace_id)
    storage_key = _validate_workspace_storage_key_for_path(project_storage_key)
    base = (projects_base or "").strip().rstrip("/")
    if not base.startswith("/"):
        raise ValueError("remote workspace_projects_base must be an absolute POSIX path")
    dir_name = workspace_project_dir_name(wid, storage_key)
    remote_path = f"{base}/{dir_name}"
    existed_before = False
    try:
        runner.run(["sh", "-lc", f"test -d {shlex.quote(remote_path)}"])
        existed_before = True
    except RuntimeError:
        existed_before = False
    runner.run(["mkdir", "-p", remote_path])
    uid, gid = workspace_container_uid_gid()
    runner.run(["chown", "-R", f"{uid}:{gid}", remote_path])
    logger.info(
        "workspace_project_host_dir_ready",
        extra={
            "host_path": remote_path,
            "workspace_id": wid,
            "project_storage_key": storage_key,
            "host_dir_name": dir_name,
            "path_mode": "legacy_workspace_id" if not storage_key else "workspace_id_plus_storage_key",
            "directory_preexisted": existed_before,
            "execution_target": "remote",
            "chown_uid": uid,
            "chown_gid": gid,
        },
    )
    return remote_path


# Same as :func:`ssh_remote_ensure_workspace_project_dir` — used when ``runner`` is SSM-backed.
remote_shell_ensure_workspace_project_dir = ssh_remote_ensure_workspace_project_dir


def ensure_code_server_bind_auth_proxy_config(cfg_host: str) -> None:
    """Seed or patch bind-mounted ``config.yaml`` for gateway + Traefik access.

    - ``auth: none``: DevNest ForwardAuth / sessions own access control; persisted ``password`` auth
      prompts users and fights the intended model.
    - ``trusted-origins``: avoids VS Code origin checks hanging when the browser ``Host`` is the
      public workspace hostname behind a reverse proxy.
    """
    host_dir = (cfg_host or "").strip()
    if not host_dir:
        return
    try:
        os.makedirs(host_dir, exist_ok=True)
    except OSError as e:
        logger.warning(
            "workspace_code_server_config_dir_unusable",
            extra={"cfg_host": host_dir, "error": str(e)},
        )
        return
    path = os.path.join(host_dir, "config.yaml")
    minimal = (
        "# DevNest: auth delegated to gateway; trusted origins for reverse-proxy access.\n"
        "auth: none\n"
        "trusted-origins:\n"
        "  - '*'\n"
    )
    try:
        if not os.path.isfile(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(minimal)
            logger.info("workspace_code_server_config_seeded", extra={"path": path})
            return
        with open(path, encoding="utf-8") as f:
            content = f.read()
        changed = False
        new_content, n_subs = re.subn(
            r"(?mi)^(\s*)auth:\s*password\b.*$",
            r"\1auth: none",
            content,
        )
        if n_subs:
            content = new_content
            changed = True
        if not re.search(r"(?mi)^\s*trusted-origins\s*:", content):
            content = content.rstrip() + "\n\ntrusted-origins:\n  - '*'\n"
            changed = True
        if changed:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("workspace_code_server_config_patched", extra={"path": path})
    except OSError as e:
        logger.warning(
            "workspace_code_server_config_seed_failed",
            extra={"path": path, "error": str(e)},
        )


def _validate_workspace_id_for_path(workspace_id: str) -> str:
    wid = (workspace_id or "").strip()
    if not wid or not _WORKSPACE_ID_SAFE.match(wid):
        raise ValueError(f"unsafe or empty workspace_id for host path: {workspace_id!r}")
    return wid


def _validate_workspace_storage_key_for_path(project_storage_key: str | None) -> str | None:
    if project_storage_key is None:
        return None
    key = str(project_storage_key).strip()
    if not key:
        return None
    if not _WORKSPACE_STORAGE_KEY_SAFE.match(key):
        raise ValueError(f"unsafe workspace project storage key for host path: {project_storage_key!r}")
    return key
