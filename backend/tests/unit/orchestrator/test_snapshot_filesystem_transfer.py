"""Unit tests: snapshot tar streaming from a Docker engine (multi-node export path)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from app.services.orchestrator_service.snapshot_filesystem import export_running_workspace_tar_from_container


def test_export_running_workspace_tar_from_container_streams_stdout(tmp_path: Path) -> None:
    def _stream():
        yield (b"chunk-a", None)
        yield (b"chunk-b", b"")
        yield (None, b"on-stderr")

    class _Api:
        def inspect_container(self, container_id: str) -> dict:
            return {"Id": container_id}

        def exec_create(self, container_id: str, cmd, stdout=True, stderr=True):
            assert "tar" in cmd
            return {"Id": "exec-1"}

        def exec_start(self, exec_id: str, stream=True, demux=True):
            assert exec_id == "exec-1"
            yield from _stream()

        def exec_inspect(self, exec_id: str) -> dict:
            return {"ExitCode": 0}

    dc = MagicMock()
    dc.api = _Api()
    dest = tmp_path / "snap.tgz"
    ok, issues = export_running_workspace_tar_from_container(
        docker_client=dc,
        container_id="abc123",
        dest=dest,
    )
    assert ok is True
    assert issues == []
    assert dest.read_bytes() == b"chunk-achunk-b"
