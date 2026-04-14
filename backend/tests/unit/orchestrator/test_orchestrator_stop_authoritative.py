"""Stop/delete require persisted container id when authoritative mode is enforced."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.libs.probes.interfaces import ProbeRunner
from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.topology.interfaces import TopologyAdapter
from app.services.orchestrator_service import DefaultOrchestratorService

WORKSPACE_ID = "55"
NODE_ID = "node-1"
TOPOLOGY_ID = 1


def _svc(mock_runtime: MagicMock, mock_topology: MagicMock, mock_probe: MagicMock, tmp_path: Path) -> DefaultOrchestratorService:
    return DefaultOrchestratorService(
        mock_runtime,
        mock_topology,
        mock_probe,
        topology_id=TOPOLOGY_ID,
        node_id=NODE_ID,
        workspace_projects_base=str(tmp_path),
    )


def test_stop_without_container_id_fails_when_authoritative(
    tmp_path: Path,
) -> None:
    mock_runtime = MagicMock(spec=RuntimeAdapter)
    mock_topology = MagicMock(spec=TopologyAdapter)
    mock_probe = MagicMock(spec=ProbeRunner)
    svc = _svc(mock_runtime, mock_topology, mock_probe, tmp_path)

    fake = type("S", (), {"devnest_env": "production", "devnest_allow_runtime_env_fallback": False})()
    with patch("app.services.placement_service.runtime_policy.get_settings", return_value=fake):
        out = svc.stop_workspace_runtime(workspace_id=WORKSPACE_ID, container_id=None)

    assert out.success is False
    assert out.issues and "authoritative_container_id_required" in out.issues[0]
    mock_runtime.inspect_container.assert_not_called()
