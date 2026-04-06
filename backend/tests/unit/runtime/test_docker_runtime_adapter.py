"""Unit tests for ``DockerRuntimeAdapter`` (Docker SDK mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock

import docker.errors
import pytest

from app.libs.runtime.docker_runtime import DockerRuntimeAdapter
from app.libs.runtime.errors import ContainerCreateError, ContainerNotFoundError, ContainerStartError, NetnsRefError


def _sample_attrs(
    *,
    cid: str = "deadbeef",
    status: str = "running",
    pid: int = 9001,
    ports: dict | None = None,
    mounts: list | None = None,
    health_status: str | None = None,
) -> dict:
    state: dict = {"Status": status, "Pid": pid}
    if health_status is not None:
        state["Health"] = {"Status": health_status}
    return {
        "Id": cid,
        "State": state,
        "NetworkSettings": {
            "Ports": ports
            if ports is not None
            else {"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18080"}]},
        },
        "Mounts": mounts
        if mounts is not None
        else [{"Type": "bind", "Source": "/host/ws", "Destination": "/home/coder/project"}],
    }


@pytest.fixture
def mock_client() -> MagicMock:
    c = MagicMock()
    c.api.create_host_config.return_value = MagicMock(name="host_config")
    return c


@pytest.fixture
def adapter(mock_client: MagicMock) -> DockerRuntimeAdapter:
    return DockerRuntimeAdapter(client=mock_client)


class TestInspectContainerNormalization:
    def test_not_found_returns_missing_snapshot(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        mock_client.containers.get.side_effect = docker.errors.NotFound("nope")

        r = adapter.inspect_container(container_id="missing")

        assert r.exists is False
        assert r.container_id is None
        assert r.container_state == "missing"
        assert r.pid is None
        assert r.ports == ()
        assert r.mounts == ()
        assert r.health_status is None

    def test_normalizes_ports_mounts_health_pid(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(
            cid="abc123full",
            status="Running",
            pid=4242,
            health_status="healthy",
        )
        mock_client.containers.get.return_value = ctr

        r = adapter.inspect_container(container_id="abc")

        assert r.exists is True
        assert r.container_id == "abc123full"
        assert r.container_state == "running"
        assert r.pid == 4242
        assert r.ports == ((18080, 8080),)
        assert r.mounts == ("/host/ws:/home/coder/project",)
        assert r.health_status == "healthy"

    def test_pid_zero_normalized_to_none(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(pid=0, status="created")
        mock_client.containers.get.return_value = ctr

        r = adapter.inspect_container(container_id="x")

        assert r.pid is None


class TestEnsureContainer:
    def test_reuses_existing_without_create(
        self, adapter: DockerRuntimeAdapter, mock_client: MagicMock
    ) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(cid="exist1", status="exited", pid=0)
        mock_client.containers.get.return_value = ctr

        r = adapter.ensure_container(name="ws-1", workspace_host_path="/should/not/matter")

        assert r.exists is True
        assert r.created_new is False
        assert r.container_id == "exist1"
        assert r.container_state == "exited"
        mock_client.containers.create.assert_not_called()

    def test_reuse_uses_synthetic_ports_when_engine_ports_empty(
        self, adapter: DockerRuntimeAdapter, mock_client: MagicMock
    ) -> None:
        ctr = MagicMock()
        attrs = _sample_attrs()
        attrs["NetworkSettings"] = {"Ports": {}}
        ctr.attrs = attrs
        mock_client.containers.get.return_value = ctr

        r = adapter.ensure_container(name="ws-1", workspace_host_path="/tmp")

        assert r.resolved_ports == ((8080, 8080),)

    def test_create_new_when_not_found(
        self,
        adapter: DockerRuntimeAdapter,
        mock_client: MagicMock,
    ) -> None:
        new_ctr = MagicMock()
        new_ctr.attrs = _sample_attrs(cid="newcid", status="created", pid=0, ports={})

        def get_side_effect(container_id: str, *a, **kw):
            if container_id == "ws-new":
                raise docker.errors.NotFound("nope")
            if container_id == "newcidfull":
                return new_ctr
            raise AssertionError(f"unexpected get: {container_id!r}")

        mock_client.containers.get.side_effect = get_side_effect
        mock_client.api.create_container.return_value = {"Id": "newcidfull"}

        r = adapter.ensure_container(
            name="ws-new",
            image="my/img:tag",
            workspace_host_path="/data/ws",
            env={"FOO": "bar"},
            labels={"k": "v"},
            ports=((9000, 8080),),
        )

        assert r.created_new is True
        assert r.container_id == "newcid"
        mock_client.api.create_container.assert_called_once()
        call_kw = mock_client.api.create_container.call_args.kwargs
        assert call_kw["image"] == "my/img:tag"
        assert call_kw["name"] == "ws-new"
        assert call_kw["environment"] == {"FOO": "bar"}
        assert call_kw["labels"] == {"k": "v"}
        assert call_kw["ports"] == [8080]
        mock_client.api.create_host_config.assert_called_once()
        hc_kwargs = mock_client.api.create_host_config.call_args.kwargs
        assert "/data/ws:/home/coder/project:rw" in hc_kwargs["binds"][0]
        assert hc_kwargs["port_bindings"]["8080/tcp"] == 9000

    def test_create_requires_workspace_host_path(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        mock_client.containers.get.side_effect = docker.errors.NotFound("nope")

        with pytest.raises(ContainerCreateError, match="workspace_host_path"):
            adapter.ensure_container(name="ws", workspace_host_path=None)

        mock_client.api.create_container.assert_not_called()

    def test_create_pulls_image_on_image_not_found(
        self,
        adapter: DockerRuntimeAdapter,
        mock_client: MagicMock,
    ) -> None:
        new_ctr = MagicMock()
        new_ctr.attrs = _sample_attrs(cid="pulled", status="created", pid=0, ports={})

        def get_side_effect(container_id: str, *a, **kw):
            if container_id == "ws":
                raise docker.errors.NotFound("nope")
            if container_id == "pulledid":
                return new_ctr
            raise AssertionError(container_id)

        mock_client.containers.get.side_effect = get_side_effect
        mock_client.api.create_container.side_effect = [
            docker.errors.ImageNotFound("pull me"),
            {"Id": "pulledid"},
        ]

        r = adapter.ensure_container(name="ws", image="x/y:z", workspace_host_path="/w")

        mock_client.images.pull.assert_called_once_with("x/y:z")
        assert r.created_new is True
        assert r.container_id == "pulled"


class TestStartContainer:
    def test_already_running_no_start_call(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.status = "running"
        ctr.attrs = _sample_attrs(status="running", pid=1)
        mock_client.containers.get.return_value = ctr

        r = adapter.start_container(container_id="cid")

        assert r.success is True
        assert r.container_state == "running"
        ctr.start.assert_not_called()

    def test_stopped_starts_then_returns_running(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.status = "exited"
        reload_count = 0

        def reload_side_effect() -> None:
            nonlocal reload_count
            reload_count += 1
            if reload_count >= 2:
                ctr.status = "running"
                ctr.attrs = _sample_attrs(status="running", pid=99)

        ctr.reload.side_effect = reload_side_effect
        mock_client.containers.get.return_value = ctr

        r = adapter.start_container(container_id="cid")

        ctr.start.assert_called_once()
        assert r.success is True
        assert r.container_state == "running"

    def test_missing_raises_container_not_found(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        mock_client.containers.get.side_effect = docker.errors.NotFound("missing")

        with pytest.raises(ContainerNotFoundError, match="gone"):
            adapter.start_container(container_id="gone")

    def test_start_api_error_wraps(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.status = "created"
        ctr.start.side_effect = docker.errors.APIError("boom")
        mock_client.containers.get.return_value = ctr

        with pytest.raises(ContainerStartError, match="boom"):
            adapter.start_container(container_id="cid")


class TestGetContainerNetnsRef:
    def test_valid_returns_proc_path(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(cid="c1", pid=12345)
        mock_client.containers.get.return_value = ctr

        r = adapter.get_container_netns_ref(container_id="c1")

        assert r.container_id == "c1"
        assert r.pid == 12345
        assert r.netns_ref == "/proc/12345/ns/net"

    def test_missing_container_raises(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        mock_client.containers.get.side_effect = docker.errors.NotFound("nope")

        with pytest.raises(NetnsRefError, match="not found"):
            adapter.get_container_netns_ref(container_id="x")

    def test_no_pid_raises(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(pid=0, status="created")
        mock_client.containers.get.return_value = ctr

        with pytest.raises(NetnsRefError, match="no host PID"):
            adapter.get_container_netns_ref(container_id="c1")


class TestDefaultImageFromEnv:
    def test_ensure_uses_env_workspace_image_when_image_omitted(
        self,
        mock_client: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_client.api.create_host_config.return_value = MagicMock(name="host_config")
        monkeypatch.setenv("DEVNEST_WORKSPACE_IMAGE", "custom/workspace:dev")

        new_ctr = MagicMock()
        new_ctr.attrs = _sample_attrs(cid="e1", status="created", pid=0, ports={})

        def get_side_effect(container_id: str, *a, **kw):
            if container_id == "n":
                raise docker.errors.NotFound("nope")
            if container_id == "e1full":
                return new_ctr
            raise AssertionError(container_id)

        mock_client.containers.get.side_effect = get_side_effect
        mock_client.api.create_container.return_value = {"Id": "e1full"}

        adapter = DockerRuntimeAdapter(client=mock_client)
        adapter.ensure_container(name="n", workspace_host_path="/p")

        assert mock_client.api.create_container.call_args.kwargs["image"] == "custom/workspace:dev"
