"""Unit tests: workspace project directory helpers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.node_execution_service.workspace_project_dir import (
    default_local_ensure_workspace_project_dir,
    ssh_remote_ensure_workspace_project_dir,
    verify_workspace_runtime_owns_path,
)


def test_default_local_creates_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = default_local_ensure_workspace_project_dir(tmp, "ws42")
        assert Path(p).is_dir()
        assert p.endswith(f"ws42")


def test_default_local_rejects_unsafe_workspace_id() -> None:
    with pytest.raises(ValueError, match="unsafe"):
        default_local_ensure_workspace_project_dir("/tmp", "../../etc")


def test_ssh_remote_requires_absolute_base() -> None:
    runner = MagicMock()
    with pytest.raises(ValueError, match="absolute POSIX"):
        ssh_remote_ensure_workspace_project_dir(runner, "relative/path", "ws1")


def test_ssh_remote_mkdir() -> None:
    runner = MagicMock()
    path = ssh_remote_ensure_workspace_project_dir(runner, "/var/devnest", "ws7")
    assert path == "/var/devnest/ws7"
    runner.run.assert_called_once_with(["mkdir", "-p", "/var/devnest/ws7"])


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
