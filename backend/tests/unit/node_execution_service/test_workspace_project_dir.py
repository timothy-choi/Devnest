"""Unit tests: workspace project directory helpers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.node_execution_service.workspace_project_dir import (
    default_local_ensure_workspace_project_dir,
    prune_orphaned_workspace_project_dirs,
    ssh_remote_ensure_workspace_project_dir,
    verify_workspace_runtime_owns_path,
    workspace_project_dir_name,
)


def test_default_local_creates_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = default_local_ensure_workspace_project_dir(tmp, "ws42")
        assert Path(p).is_dir()
        assert p.endswith(f"ws42")


def test_default_local_uses_storage_key_for_isolated_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = default_local_ensure_workspace_project_dir(tmp, "1", "abc123")
        assert Path(p).is_dir()
        assert p.endswith("1-abc123")


def test_default_local_resume_missing_raises_without_creating() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "9-deadbeef"
        with pytest.raises(ValueError, match="missing for resume"):
            default_local_ensure_workspace_project_dir(tmp, "9", "deadbeef", allow_create=False)
        assert not missing.exists()


def test_default_local_resume_allows_existing_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = default_local_ensure_workspace_project_dir(tmp, "3", "aa", allow_create=True)
        p2 = default_local_ensure_workspace_project_dir(tmp, "3", "aa", allow_create=False)
        assert p == p2


def test_workspace_project_dir_name_changes_when_storage_key_changes() -> None:
    assert workspace_project_dir_name("1", "key-a") != workspace_project_dir_name("1", "key-b")
    assert workspace_project_dir_name("1", "key-a") == workspace_project_dir_name("1", "key-a")


def test_prune_orphaned_workspace_project_dirs_removes_unreferenced_dirs(tmp_path: Path) -> None:
    keep = tmp_path / "1-active"
    stale = tmp_path / "1"
    keep.mkdir()
    stale.mkdir()

    removed = prune_orphaned_workspace_project_dirs(str(tmp_path), [("1", "active")])

    assert str(stale) in removed
    assert keep.exists()
    assert not stale.exists()


def test_prune_orphaned_workspace_project_dirs_cleans_all_when_db_reset(tmp_path: Path) -> None:
    stale_a = tmp_path / "1"
    stale_b = tmp_path / "2-deadbeef"
    stale_a.mkdir()
    stale_b.mkdir()

    removed = prune_orphaned_workspace_project_dirs(str(tmp_path), [])

    assert set(removed) == {str(stale_a), str(stale_b)}
    assert not stale_a.exists()
    assert not stale_b.exists()


def test_default_local_rejects_unsafe_workspace_id() -> None:
    with pytest.raises(ValueError, match="unsafe"):
        default_local_ensure_workspace_project_dir("/tmp", "../../etc")


def test_ssh_remote_requires_absolute_base() -> None:
    runner = MagicMock()
    with pytest.raises(ValueError, match="absolute POSIX"):
        ssh_remote_ensure_workspace_project_dir(runner, "relative/path", "ws1")


def test_ssh_remote_resume_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVNEST_WORKSPACE_CONTAINER_UID", "1000")
    monkeypatch.setenv("DEVNEST_WORKSPACE_CONTAINER_GID", "1000")
    runner = MagicMock()
    runner.run.side_effect = RuntimeError("no dir")
    with pytest.raises(ValueError, match="missing on execution host"):
        ssh_remote_ensure_workspace_project_dir(runner, "/var/devnest", "ws7", "k1", allow_create=False)


def test_ssh_remote_mkdir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVNEST_WORKSPACE_CONTAINER_UID", "1000")
    monkeypatch.setenv("DEVNEST_WORKSPACE_CONTAINER_GID", "1000")
    runner = MagicMock()
    # First call is ``test -d`` (missing dir); then ``mkdir -p``; then ``chown``.
    runner.run.side_effect = [RuntimeError("missing"), None, None]
    path = ssh_remote_ensure_workspace_project_dir(runner, "/var/devnest", "ws7", "k1")
    assert path == "/var/devnest/ws7-k1"
    assert runner.run.call_count == 3
    assert runner.run.call_args_list[-2].args[0] == ["mkdir", "-p", "/var/devnest/ws7-k1"]
    assert runner.run.call_args_list[-1].args[0] == ["chown", "-R", "1000:1000", "/var/devnest/ws7-k1"]


def test_verify_workspace_runtime_owns_path_rejects_wrong_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVNEST_WORKSPACE_CONTAINER_UID", "1000")
    monkeypatch.setenv("DEVNEST_WORKSPACE_CONTAINER_GID", "1000")
    d = tmp_path / "x"
    d.mkdir()
    try:
        os.chown(d, 0, 0)
    except PermissionError:
        pytest.skip("need root to chown to 0:0 for this assertion")
    with pytest.raises(OSError, match="not owned by runtime user"):
        verify_workspace_runtime_owns_path(str(d))
