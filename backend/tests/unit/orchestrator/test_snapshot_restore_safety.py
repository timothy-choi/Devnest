"""Unit tests for snapshot restore safety (Task 3: path traversal, atomicity)."""

from __future__ import annotations

import os
import tarfile
import tempfile
from pathlib import Path

import pytest

from app.services.orchestrator_service.errors import WorkspaceSnapshotError
from app.services.orchestrator_service.service import DefaultOrchestratorService


def _write_tar(dest: Path, members: list[tuple[str, bytes]]) -> None:
    """Create a .tar.gz archive with the given (arcname, content) entries."""
    with tarfile.open(dest, "w:gz", format=tarfile.PAX_FORMAT) as tf:
        for arcname, content in members:
            import io  # noqa: PLC0415
            buf = io.BytesIO(content)
            ti = tarfile.TarInfo(name=arcname)
            ti.size = len(content)
            tf.addfile(ti, buf)


class TestValidateTarMembers:
    def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        archive = tmp_path / "bad.tar.gz"
        _write_tar(archive, [("/etc/passwd", b"hack")])
        dest = tmp_path / "dest"
        dest.mkdir()
        with tarfile.open(archive, "r:gz") as tf:
            with pytest.raises(WorkspaceSnapshotError, match="unsafe_path"):
                DefaultOrchestratorService._validate_tar_members(tf, dest)

    def test_rejects_dotdot_traversal(self, tmp_path: Path) -> None:
        archive = tmp_path / "traversal.tar.gz"
        _write_tar(archive, [("../escape.txt", b"bad")])
        dest = tmp_path / "dest"
        dest.mkdir()
        with tarfile.open(archive, "r:gz") as tf:
            with pytest.raises(WorkspaceSnapshotError, match="unsafe_path"):
                DefaultOrchestratorService._validate_tar_members(tf, dest)

    def test_accepts_safe_relative_paths(self, tmp_path: Path) -> None:
        archive = tmp_path / "safe.tar.gz"
        _write_tar(archive, [("a/b/c.txt", b"content"), ("readme.md", b"hi")])
        dest = tmp_path / "dest"
        dest.mkdir()
        with tarfile.open(archive, "r:gz") as tf:
            # Should not raise
            DefaultOrchestratorService._validate_tar_members(tf, dest)


class TestSafeSnapshotTarExtract:
    def test_extracts_valid_archive_to_dest(self, tmp_path: Path) -> None:
        archive = tmp_path / "snapshot.tar.gz"
        _write_tar(archive, [("project/main.py", b"print('hello')"), ("README.md", b"readme")])
        dest = tmp_path / "workspace"
        dest.mkdir()
        with tarfile.open(archive, "r:gz") as tf:
            DefaultOrchestratorService._safe_snapshot_tar_extract(tf, dest)
        assert (dest / "project" / "main.py").is_file()
        assert (dest / "README.md").is_file()

    def test_atomic_restore_preserves_on_failure(self, tmp_path: Path) -> None:
        """Original dest is preserved when extraction fails (e.g. path traversal)."""
        # Set up original content
        dest = tmp_path / "workspace"
        dest.mkdir()
        original_file = dest / "original.py"
        original_file.write_text("original content")

        archive = tmp_path / "bad.tar.gz"
        _write_tar(archive, [("../escape.txt", b"malicious")])
        with tarfile.open(archive, "r:gz") as tf:
            with pytest.raises(WorkspaceSnapshotError):
                DefaultOrchestratorService._safe_snapshot_tar_extract(tf, dest)

        # Original content must still be there
        assert original_file.is_file()
        assert original_file.read_text() == "original content"

    def test_atomic_restore_replaces_existing_files(self, tmp_path: Path) -> None:
        """Successful restore atomically replaces dest with archive contents."""
        dest = tmp_path / "workspace"
        dest.mkdir()
        (dest / "old_file.py").write_text("old")

        archive = tmp_path / "new.tar.gz"
        _write_tar(archive, [("new_file.py", b"new content")])
        with tarfile.open(archive, "r:gz") as tf:
            DefaultOrchestratorService._safe_snapshot_tar_extract(tf, dest)

        # Old file replaced by new archive contents
        assert (dest / "new_file.py").is_file()

    def test_no_temp_dirs_left_on_success(self, tmp_path: Path) -> None:
        """No .devnest-restore-tmp-* dirs should remain after successful extract."""
        dest = tmp_path / "workspace"
        dest.mkdir()
        archive = tmp_path / "ok.tar.gz"
        _write_tar(archive, [("file.txt", b"data")])
        with tarfile.open(archive, "r:gz") as tf:
            DefaultOrchestratorService._safe_snapshot_tar_extract(tf, dest)
        leftovers = list(tmp_path.glob(".devnest-restore-tmp-*"))
        assert leftovers == [], f"Temp dirs not cleaned up: {leftovers}"

    def test_no_temp_dirs_left_on_failure(self, tmp_path: Path) -> None:
        """No .devnest-restore-tmp-* dirs remain after failed extract."""
        dest = tmp_path / "workspace"
        dest.mkdir()
        archive = tmp_path / "bad.tar.gz"
        _write_tar(archive, [("../traversal.txt", b"bad")])
        with tarfile.open(archive, "r:gz") as tf:
            with pytest.raises(WorkspaceSnapshotError):
                DefaultOrchestratorService._safe_snapshot_tar_extract(tf, dest)
        leftovers = list(tmp_path.glob(".devnest-restore-tmp-*"))
        assert leftovers == [], f"Temp dirs not cleaned up: {leftovers}"
