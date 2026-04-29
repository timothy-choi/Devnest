"""Unit tests for code-server integration in the orchestrator (Task 13)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.libs.probes.interfaces import ProbeRunner
from app.libs.probes.results import ServiceProbeResult, WorkspaceHealthResult
from app.libs.runtime.interfaces import RuntimeAdapter
from app.libs.runtime.models import (
    CODE_SERVER_CONFIG_CONTAINER_PATH,
    CODE_SERVER_DATA_CONTAINER_PATH,
    WORKSPACE_IDE_CONTAINER_PORT,
    ContainerInspectionResult,
    NetnsRefResult,
    RuntimeActionResult,
    RuntimeEnsureResult,
)
from app.libs.topology.interfaces import TopologyAdapter
from app.libs.topology.models.enums import TopologyRuntimeStatus
from app.libs.topology.results import (
    AllocateWorkspaceIPResult,
    AttachWorkspaceResult,
    EnsureNodeTopologyResult,
)
from app.services.node_execution_service.workspace_project_dir import WORKSPACE_USER_PROJECT_SUBDIR
from app.services.orchestrator_service import DefaultOrchestratorService


WORKSPACE_ID = "42"
CONTAINER_ID = "ctr-code-server"
NODE_ID = "node-cs"
TOPOLOGY_ID = 1
WORKSPACE_IP = "10.128.1.10"
INTERNAL_ENDPOINT = f"{WORKSPACE_IP}:8080"
NETNS_REF = "/proc/99999/ns/net"


def _make_runtime() -> MagicMock:
    rt = MagicMock(spec=RuntimeAdapter)
    rt.ensure_container.return_value = RuntimeEnsureResult(
        container_id=CONTAINER_ID,
        exists=True,
        created_new=True,
        container_state="running",
        resolved_ports=((32100, WORKSPACE_IDE_CONTAINER_PORT),),
    )
    rt.start_container.return_value = RuntimeActionResult(
        container_id=CONTAINER_ID, container_state="running", success=True
    )
    rt.inspect_container.return_value = ContainerInspectionResult(
        exists=True,
        container_id=CONTAINER_ID,
        container_state="running",
        pid=99999,
        ports=((32100, WORKSPACE_IDE_CONTAINER_PORT),),
        mounts=(),
    )
    rt.get_container_netns_ref.return_value = NetnsRefResult(
        container_id=CONTAINER_ID, pid=99999, netns_ref=NETNS_REF
    )
    rt.fetch_container_log_tail.return_value = ""
    return rt


def _make_topology() -> MagicMock:
    topo = MagicMock(spec=TopologyAdapter)
    topo.ensure_node_topology.return_value = EnsureNodeTopologyResult(
        topology_runtime_id=1,
        bridge_name="br-cs",
        cidr="10.128.1.0/24",
        gateway_ip="10.128.1.1",
        status=TopologyRuntimeStatus.READY,
    )
    topo.allocate_workspace_ip.return_value = AllocateWorkspaceIPResult(
        workspace_ip=WORKSPACE_IP, leased_existing=False
    )
    topo.attach_workspace.return_value = AttachWorkspaceResult(
        attachment_id=1,
        workspace_ip=WORKSPACE_IP,
        bridge_name="br-cs",
        gateway_ip="10.128.1.1",
        internal_endpoint=INTERNAL_ENDPOINT,
    )
    return topo


def _make_probe() -> MagicMock:
    probe = MagicMock(spec=ProbeRunner)
    probe.check_service_reachable.return_value = ServiceProbeResult(
        healthy=True,
        workspace_ip=WORKSPACE_IP,
        port=WORKSPACE_IDE_CONTAINER_PORT,
        latency_ms=1.0,
        issues=(),
    )
    probe.check_workspace_health.return_value = WorkspaceHealthResult(
        workspace_id=int(WORKSPACE_ID),
        healthy=True,
        runtime_healthy=True,
        topology_healthy=True,
        service_healthy=True,
        container_state="running",
        workspace_ip=WORKSPACE_IP,
        internal_endpoint=INTERNAL_ENDPOINT,
        issues=(),
    )
    return probe


def _make_svc(tmp_path: Path) -> DefaultOrchestratorService:
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    return DefaultOrchestratorService(
        _make_runtime(),
        _make_topology(),
        _make_probe(),
        topology_id=TOPOLOGY_ID,
        node_id=NODE_ID,
        workspace_projects_base=str(ws_root),
    )


class TestCodeServerEnv:
    def test_code_server_env_keys_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEVNEST_WORKSPACE_AUTH_MODE", raising=False)
        monkeypatch.delenv("DEVNEST_WORKSPACE_PASSWORD", raising=False)
        from app.libs.common.config import get_settings

        get_settings.cache_clear()
        env = DefaultOrchestratorService._code_server_env()
        assert env["CODE_SERVER_AUTH"] == "none"
        assert env["DEVNEST_WORKSPACE_AUTH_MODE"] == "none"
        assert "DEVNEST_WORKSPACE_PASSWORD" not in env
        assert "PASSWORD" not in env
        assert "PORT" in env
        assert env["PORT"] == str(WORKSPACE_IDE_CONTAINER_PORT)
        assert "CS_DISABLE_GETTING_STARTED_OVERRIDE" in env
        get_settings.cache_clear()

    def test_code_server_env_custom_port(self) -> None:
        env = DefaultOrchestratorService._code_server_env(port=9090)
        assert env["PORT"] == "9090"

    def test_code_server_env_password_mode_requires_password_on_bringup(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DEVNEST_WORKSPACE_AUTH_MODE", "password")
        monkeypatch.delenv("DEVNEST_WORKSPACE_PASSWORD", raising=False)
        from app.libs.common.config import get_settings

        get_settings.cache_clear()
        svc = _make_svc(tmp_path)
        with pytest.raises(Exception, match="DEVNEST_WORKSPACE_PASSWORD"):
            svc.bring_up_workspace_runtime(workspace_id=WORKSPACE_ID)
        get_settings.cache_clear()


class TestCodeServerBindMounts:
    def test_bind_mounts_created_with_workspace_base(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        mounts = svc._code_server_extra_bind_mounts("42")
        assert len(mounts) == 2
        container_paths = {m.container_path for m in mounts}
        assert CODE_SERVER_CONFIG_CONTAINER_PATH in container_paths
        assert CODE_SERVER_DATA_CONTAINER_PATH in container_paths

    def test_bind_mount_host_dirs_created(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        mounts = svc._code_server_extra_bind_mounts("99")
        for m in mounts:
            assert Path(m.host_path).is_dir()
        cfg = tmp_path / "workspaces" / "99" / "code-server" / "config" / "config.yaml"
        assert cfg.is_file()

    def test_no_mounts_when_wid_empty_and_no_base(self) -> None:
        """Empty workspace_id returns empty mounts."""
        svc = DefaultOrchestratorService(
            _make_runtime(), _make_topology(), _make_probe(),
            workspace_projects_base="/tmp/devnest-test-nowid",
        )
        mounts = svc._code_server_extra_bind_mounts("")
        assert mounts == []

    def test_no_mounts_when_wid_empty(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        mounts = svc._code_server_extra_bind_mounts("")
        assert mounts == []

    def test_new_workspace_clears_stale_code_server_state_but_keeps_extensions(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        project_root = tmp_path / "workspaces" / "42-key"
        data_root = project_root / "code-server" / "data"
        (data_root / "User" / "workspaceStorage").mkdir(parents=True, exist_ok=True)
        (data_root / "User" / "workspaceStorage" / "stale.txt").write_text("old")
        (data_root / "History").mkdir(parents=True, exist_ok=True)
        (data_root / "History" / "old.txt").write_text("old")
        (data_root / "extensions").mkdir(parents=True, exist_ok=True)
        (data_root / "extensions" / "keep.txt").write_text("keep")

        mounts = svc._code_server_extra_bind_mounts(
            "42",
            str(project_root),
            "new",
        )

        assert len(mounts) == 2
        assert not (data_root / "User").exists()
        assert not (data_root / "History").exists()
        assert (data_root / "extensions" / "keep.txt").exists()

    def test_resume_workspace_preserves_code_server_state(self, tmp_path: Path) -> None:
        svc = _make_svc(tmp_path)
        project_root = tmp_path / "workspaces" / "42-key"
        data_root = project_root / "code-server" / "data"
        (data_root / "User" / "workspaceStorage").mkdir(parents=True, exist_ok=True)
        stale = data_root / "User" / "workspaceStorage" / "stale.txt"
        stale.write_text("old")

        mounts = svc._code_server_extra_bind_mounts(
            "42",
            str(project_root),
            "resume",
        )

        assert len(mounts) == 2
        assert stale.exists()

    def test_fallback_uses_project_storage_key_when_host_path_missing(self, tmp_path: Path) -> None:
        """Without workspace_host_path, resolve the same keyed dir as primary project mounts."""
        svc = _make_svc(tmp_path)
        mounts = svc._code_server_extra_bind_mounts("7", None, "resume", "mykey")
        assert len(mounts) == 2
        cfg = tmp_path / "workspaces" / "7-mykey" / "code-server" / "config" / "config.yaml"
        assert cfg.is_file()

    def test_code_server_persistence_sibling_to_project_subdir(self, tmp_path: Path) -> None:
        """When the IDE bind is ``…/<bundle>/project``, code-server dirs stay under ``…/<bundle>/code-server``."""
        svc = _make_svc(tmp_path)
        bundle = tmp_path / "workspaces" / "v2ws"
        ide = bundle / WORKSPACE_USER_PROJECT_SUBDIR
        ide.mkdir(parents=True)
        mounts = svc._code_server_extra_bind_mounts("v2ws", str(ide), "resume")
        assert len(mounts) == 2
        cfg = bundle / "code-server" / "config" / "config.yaml"
        assert cfg.is_file()
        assert not (ide / "code-server").exists()


class TestCodeServerBringUp:
    def test_bring_up_passes_code_server_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """bring_up_workspace_runtime injects code-server env defaults."""
        monkeypatch.delenv("DEVNEST_WORKSPACE_AUTH_MODE", raising=False)
        monkeypatch.delenv("DEVNEST_WORKSPACE_PASSWORD", raising=False)
        from app.libs.common.config import get_settings

        get_settings.cache_clear()
        rt = _make_runtime()
        svc = DefaultOrchestratorService(
            rt, _make_topology(), _make_probe(),
            topology_id=TOPOLOGY_ID, node_id=NODE_ID,
            workspace_projects_base=str(tmp_path / "workspaces"),
        )
        svc.bring_up_workspace_runtime(workspace_id=WORKSPACE_ID)
        ensure_kwargs = rt.ensure_container.call_args.kwargs
        env = ensure_kwargs.get("env") or {}
        assert env.get("CODE_SERVER_AUTH") == "none"
        assert env.get("DEVNEST_WORKSPACE_AUTH_MODE") == "none"
        assert env.get("DEVNEST_WORKSPACE_ID") == WORKSPACE_ID
        assert env.get("DEVNEST_NODE_KEY") == NODE_ID
        assert "PASSWORD" not in env
        assert "DEVNEST_WORKSPACE_PASSWORD" not in env
        assert env.get("PORT") == str(WORKSPACE_IDE_CONTAINER_PORT)
        get_settings.cache_clear()

    def test_bring_up_passes_code_server_bind_mounts(self, tmp_path: Path) -> None:
        """bring_up_workspace_runtime includes code-server persistence bind mounts."""
        ws_root = tmp_path / "workspaces"
        ws_root.mkdir()
        rt = _make_runtime()
        svc = DefaultOrchestratorService(
            rt, _make_topology(), _make_probe(),
            topology_id=TOPOLOGY_ID, node_id=NODE_ID,
            workspace_projects_base=str(ws_root),
        )
        svc.bring_up_workspace_runtime(workspace_id=WORKSPACE_ID)
        ensure_kwargs = rt.ensure_container.call_args.kwargs
        extra = ensure_kwargs.get("extra_bind_mounts") or []
        container_paths = {m.container_path for m in extra}
        assert CODE_SERVER_CONFIG_CONTAINER_PATH in container_paths
        assert CODE_SERVER_DATA_CONTAINER_PATH in container_paths
        host_paths = {m.host_path for m in extra}
        assert any(str(p).replace("\\", "/").endswith(f"/{WORKSPACE_ID}/code-server/config") for p in host_paths)
        assert any(str(p).replace("\\", "/").endswith(f"/{WORKSPACE_ID}/code-server/data") for p in host_paths)

    def test_bring_up_merges_caller_env_over_defaults(self, tmp_path: Path) -> None:
        """Caller-supplied env can enable password auth explicitly."""
        ws_root = tmp_path / "workspaces"
        ws_root.mkdir()
        rt = _make_runtime()
        svc = DefaultOrchestratorService(
            rt, _make_topology(), _make_probe(),
            topology_id=TOPOLOGY_ID, node_id=NODE_ID,
            workspace_projects_base=str(ws_root),
        )
        svc.bring_up_workspace_runtime(
            workspace_id=WORKSPACE_ID,
            env={
                "CODE_SERVER_AUTH": "password",
                "DEVNEST_WORKSPACE_AUTH_MODE": "password",
                "DEVNEST_WORKSPACE_PASSWORD": "pw-unit",
                "MY_VAR": "hello",
            },
        )
        ensure_kwargs = rt.ensure_container.call_args.kwargs
        env = ensure_kwargs.get("env") or {}
        # Caller value wins
        assert env["CODE_SERVER_AUTH"] == "password"
        assert env["DEVNEST_WORKSPACE_AUTH_MODE"] == "password"
        assert env["DEVNEST_WORKSPACE_PASSWORD"] == "pw-unit"
        # Caller extra key is preserved
        assert env["MY_VAR"] == "hello"
        # Default keys still present
        assert "CS_DISABLE_GETTING_STARTED_OVERRIDE" in env

    def test_remote_ec2_bring_up_defaults_to_no_password(self, tmp_path: Path) -> None:
        ws_root = tmp_path / "workspaces"
        ws_root.mkdir()
        rt = _make_runtime()
        probe = _make_probe()
        probe.check_container_running.return_value = MagicMock(healthy=True, issues=())
        probe.check_service_http.return_value = ServiceProbeResult(
            healthy=True,
            workspace_ip="127.0.0.1",
            port=32100,
            latency_ms=1.0,
            issues=(),
        )
        svc = DefaultOrchestratorService(
            rt,
            _make_topology(),
            probe,
            topology_id=TOPOLOGY_ID,
            node_id="ec2-autoscale-test",
            workspace_projects_base=str(ws_root),
            remote_topology_attach_deferred=True,
            traefik_routing_host="172.30.1.10",
        )
        svc.bring_up_workspace_runtime(workspace_id=WORKSPACE_ID)
        env = rt.ensure_container.call_args.kwargs.get("env") or {}
        assert env["DEVNEST_WORKSPACE_AUTH_MODE"] == "none"
        assert env["CODE_SERVER_AUTH"] == "none"
        assert env["DEVNEST_NODE_KEY"] == "ec2-autoscale-test"
        assert "PASSWORD" not in env
        assert "DEVNEST_WORKSPACE_PASSWORD" not in env

    def test_bring_up_passes_cpu_memory_limits(self, tmp_path: Path) -> None:
        ws_root = tmp_path / "workspaces"
        ws_root.mkdir()
        rt = _make_runtime()
        svc = DefaultOrchestratorService(
            rt, _make_topology(), _make_probe(),
            topology_id=TOPOLOGY_ID, node_id=NODE_ID,
            workspace_projects_base=str(ws_root),
        )
        svc.bring_up_workspace_runtime(
            workspace_id=WORKSPACE_ID,
            cpu_limit_cores=2.0,
            memory_limit_mib=512,
        )
        ensure_kwargs = rt.ensure_container.call_args.kwargs
        assert ensure_kwargs["cpu_limit"] == 2.0
        assert ensure_kwargs["memory_limit_bytes"] == 512 * 1024 * 1024
