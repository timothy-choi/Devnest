"""Unit tests for ``DockerRuntimeAdapter`` (Docker SDK mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock

import docker.errors
import pytest

from app.libs.runtime.docker_runtime import DockerRuntimeAdapter
from app.libs.runtime.errors import (
    ContainerCreateError,
    ContainerDeleteError,
    ContainerNotFoundError,
    ContainerStartError,
    ContainerStopError,
    NetnsRefError,
)


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
    def test_api_error_other_than_not_found_propagates(
        self, adapter: DockerRuntimeAdapter, mock_client: MagicMock
    ) -> None:
        mock_client.containers.get.side_effect = docker.errors.APIError("engine down")

        with pytest.raises(docker.errors.APIError, match="engine down"):
            adapter.inspect_container(container_id="any")

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

    def test_empty_engine_status_becomes_unknown(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(status="", pid=100)
        mock_client.containers.get.return_value = ctr

        r = adapter.inspect_container(container_id="x")

        assert r.container_state == "unknown"

    def test_multiple_ports_sorted_by_host_then_container(
        self, adapter: DockerRuntimeAdapter, mock_client: MagicMock
    ) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(
            ports={
                "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3000"}],
                "9000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "4000"}],
            },
        )
        mock_client.containers.get.return_value = ctr

        r = adapter.inspect_container(container_id="x")

        assert r.ports == ((3000, 8080), (4000, 9000))

    def test_skips_bindings_without_numeric_host_port(
        self, adapter: DockerRuntimeAdapter, mock_client: MagicMock
    ) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(
            ports={
                "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": ""}],
                "9000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "1111"}],
            },
        )
        mock_client.containers.get.return_value = ctr

        r = adapter.inspect_container(container_id="x")

        assert r.ports == ((1111, 9000),)

    def test_host_port_with_whitespace_is_accepted(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(
            ports={"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": " 18080 "}]},
        )
        mock_client.containers.get.return_value = ctr

        r = adapter.inspect_container(container_id="x")

        assert r.ports == ((18080, 8080),)

    def test_mounts_skip_non_dict_entries(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        attrs = _sample_attrs()
        attrs["Mounts"] = ["not-a-dict", {"Type": "bind", "Source": "/a", "Destination": "/b"}]
        ctr.attrs = attrs
        mock_client.containers.get.return_value = ctr

        r = adapter.inspect_container(container_id="x")

        assert r.mounts == ("/a:/b",)

    def test_destination_only_mount_when_no_source(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        attrs = _sample_attrs(mounts=[{"Type": "volume", "Source": "", "Destination": "/data"}])
        ctr.attrs = attrs
        mock_client.containers.get.return_value = ctr

        r = adapter.inspect_container(container_id="x")

        assert r.mounts == ("/data",)


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
        assert r.workspace_ide_container_port == 8080
        mock_client.containers.create.assert_not_called()

    def test_reuses_existing_when_existing_container_id_resolves(
        self, adapter: DockerRuntimeAdapter, mock_client: MagicMock
    ) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(cid="realid", status="exited", pid=0)

        def get_side_effect(ref: str, *a, **kw):
            if ref == "fullcontainerid":
                return ctr
            if ref == "logical-name":
                raise docker.errors.NotFound("nope")
            raise AssertionError(ref)

        mock_client.containers.get.side_effect = get_side_effect

        r = adapter.ensure_container(
            name="logical-name",
            existing_container_id="fullcontainerid",
            workspace_host_path="/unused",
        )

        assert r.created_new is False
        assert r.container_id == "realid"
        assert mock_client.containers.get.call_args_list[0][0][0] == "fullcontainerid"

    def test_reuse_uses_synthetic_ports_when_engine_ports_empty(
        self, adapter: DockerRuntimeAdapter, mock_client: MagicMock
    ) -> None:
        ctr = MagicMock()
        attrs = _sample_attrs()
        attrs["NetworkSettings"] = {"Ports": {}}
        ctr.attrs = attrs
        mock_client.containers.get.return_value = ctr

        r = adapter.ensure_container(name="ws-1", workspace_host_path="/tmp")

        assert r.resolved_ports == ()

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

    def test_create_omits_host_publish_when_ports_not_specified(
        self, adapter: DockerRuntimeAdapter, mock_client: MagicMock
    ) -> None:
        new_ctr = MagicMock()
        new_ctr.attrs = _sample_attrs(cid="ephem", status="created", pid=0, ports={})

        def get_side_effect(container_id: str, *a, **kw):
            if container_id == "ws-eph":
                raise docker.errors.NotFound("nope")
            if container_id == "ephemfull":
                return new_ctr
            raise AssertionError(container_id)

        mock_client.containers.get.side_effect = get_side_effect
        mock_client.api.create_container.return_value = {"Id": "ephemfull"}

        r = adapter.ensure_container(name="ws-eph", workspace_host_path="/proj")

        hc_kwargs = mock_client.api.create_host_config.call_args.kwargs
        assert "port_bindings" not in hc_kwargs
        assert mock_client.api.create_container.call_args.kwargs["ports"] == []
        assert r.workspace_ide_container_port == 8080
        assert r.resolved_ports == ()

    def test_create_explicit_zero_host_port_is_ephemeral(
        self, adapter: DockerRuntimeAdapter, mock_client: MagicMock
    ) -> None:
        new_ctr = MagicMock()
        new_ctr.attrs = _sample_attrs(cid="z", status="created", pid=0, ports={})

        def get_side_effect(container_id: str, *a, **kw):
            if container_id == "ws-z":
                raise docker.errors.NotFound("nope")
            if container_id == "zfull":
                return new_ctr
            raise AssertionError(container_id)

        mock_client.containers.get.side_effect = get_side_effect
        mock_client.api.create_container.return_value = {"Id": "zfull"}

        adapter.ensure_container(name="ws-z", workspace_host_path="/p", ports=((0, 8080),))

        hc_kwargs = mock_client.api.create_host_config.call_args.kwargs
        assert hc_kwargs["port_bindings"]["8080/tcp"] is None

    def test_create_duplicate_container_port_uses_last_host_binding(
        self, adapter: DockerRuntimeAdapter, mock_client: MagicMock
    ) -> None:
        new_ctr = MagicMock()
        new_ctr.attrs = _sample_attrs(cid="dup", status="created", pid=0, ports={})

        def get_side_effect(container_id: str, *a, **kw):
            if container_id == "ws-dup":
                raise docker.errors.NotFound("nope")
            if container_id == "dupfull":
                return new_ctr
            raise AssertionError(container_id)

        mock_client.containers.get.side_effect = get_side_effect
        mock_client.api.create_container.return_value = {"Id": "dupfull"}

        adapter.ensure_container(
            name="ws-dup",
            workspace_host_path="/w",
            ports=((1111, 8080), (2222, 8080)),
        )

        hc_kwargs = mock_client.api.create_host_config.call_args.kwargs
        assert hc_kwargs["port_bindings"]["8080/tcp"] == 2222

    def test_create_requires_workspace_host_path(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        mock_client.containers.get.side_effect = docker.errors.NotFound("nope")

        with pytest.raises(ContainerCreateError, match="workspace_host_path"):
            adapter.ensure_container(name="ws", workspace_host_path=None)

        mock_client.api.create_container.assert_not_called()

    def test_create_rejects_non_positive_cpu_limit(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        mock_client.containers.get.side_effect = docker.errors.NotFound("nope")

        with pytest.raises(ContainerCreateError, match="cpu_limit"):
            adapter.ensure_container(name="ws", workspace_host_path="/w", cpu_limit=0.0)

        mock_client.api.create_container.assert_not_called()

    def test_create_rejects_non_positive_memory_limit(
        self, adapter: DockerRuntimeAdapter, mock_client: MagicMock
    ) -> None:
        mock_client.containers.get.side_effect = docker.errors.NotFound("nope")

        with pytest.raises(ContainerCreateError, match="memory_limit_bytes"):
            adapter.ensure_container(name="ws", workspace_host_path="/w", memory_limit_bytes=0)

        mock_client.api.create_container.assert_not_called()

    def test_create_passes_nano_cpus_when_cpu_limit_set(
        self, adapter: DockerRuntimeAdapter, mock_client: MagicMock
    ) -> None:
        new_ctr = MagicMock()
        new_ctr.attrs = _sample_attrs(cid="cpu", status="created", pid=0, ports={})

        def get_side_effect(container_id: str, *a, **kw):
            if container_id == "ws-cpu":
                raise docker.errors.NotFound("nope")
            if container_id == "cpufull":
                return new_ctr
            raise AssertionError(container_id)

        mock_client.containers.get.side_effect = get_side_effect
        mock_client.api.create_container.return_value = {"Id": "cpufull"}

        adapter.ensure_container(name="ws-cpu", workspace_host_path="/w", cpu_limit=0.5)

        hc = mock_client.api.create_host_config.call_args.kwargs
        assert hc["nano_cpus"] == 500_000_000

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
        ctr.attrs = _sample_attrs(status="running", pid=1)
        mock_client.containers.get.return_value = ctr

        r = adapter.start_container(container_id="cid")

        assert r.success is True
        assert r.container_state == "running"
        ctr.start.assert_not_called()

    def test_stopped_starts_then_returns_running(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(status="exited", pid=0)

        def start_side_effect(*_a, **_kw) -> None:
            ctr.attrs = _sample_attrs(status="running", pid=99)

        ctr.start.side_effect = start_side_effect
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
        ctr.attrs = _sample_attrs(status="created", pid=0)
        ctr.start.side_effect = docker.errors.APIError("boom")
        mock_client.containers.get.return_value = ctr

        with pytest.raises(ContainerStartError, match="boom"):
            adapter.start_container(container_id="cid")

    def test_start_succeeds_false_when_still_not_running_after_start(
        self, adapter: DockerRuntimeAdapter, mock_client: MagicMock
    ) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(status="created", pid=0)
        ctr.start.return_value = None
        mock_client.containers.get.return_value = ctr

        r = adapter.start_container(container_id="cid")

        ctr.start.assert_called_once()
        assert r.success is False
        assert r.container_state == "created"
        assert r.message is not None and "unexpected state" in r.message


class TestStopContainer:
    def test_missing_returns_idempotent_success(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        mock_client.containers.get.side_effect = docker.errors.NotFound("nope")

        r = adapter.stop_container(container_id="gone")

        assert r.success is True
        assert r.container_state == "missing"
        assert r.container_id == "gone"

    def test_exited_does_not_call_engine_stop(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(status="exited", pid=0)
        mock_client.containers.get.return_value = ctr

        r = adapter.stop_container(container_id="cid")

        assert r.success is True
        assert r.container_state == "exited"
        ctr.stop.assert_not_called()

    def test_running_stops_then_reinspect_exited(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(status="running", pid=1)

        def stop_side_effect(*_a, **kw) -> None:
            ctr.attrs = _sample_attrs(status="exited", pid=0)

        ctr.stop.side_effect = stop_side_effect
        mock_client.containers.get.return_value = ctr

        r = adapter.stop_container(container_id="cid")

        ctr.stop.assert_called_once_with(timeout=10)
        assert r.success is True
        assert r.container_state == "exited"

    def test_paused_triggers_stop(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(status="paused", pid=1)

        def stop_side_effect(*_a, **kw) -> None:
            ctr.attrs = _sample_attrs(status="exited", pid=0)

        ctr.stop.side_effect = stop_side_effect
        mock_client.containers.get.return_value = ctr

        r = adapter.stop_container(container_id="cid")

        ctr.stop.assert_called_once_with(timeout=10)
        assert r.success is True
        assert r.container_state == "exited"

    def test_restarting_triggers_stop(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(status="restarting", pid=1)

        def stop_side_effect(*_a, **kw) -> None:
            ctr.attrs = _sample_attrs(status="exited", pid=0)

        ctr.stop.side_effect = stop_side_effect
        mock_client.containers.get.return_value = ctr

        r = adapter.stop_container(container_id="cid")

        ctr.stop.assert_called_once_with(timeout=10)
        assert r.success is True
        assert r.container_state == "exited"

    def test_stop_api_error_raises(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(status="running", pid=1)
        ctr.stop.side_effect = docker.errors.APIError("stop failed")
        mock_client.containers.get.return_value = ctr

        with pytest.raises(ContainerStopError, match="stop failed"):
            adapter.stop_container(container_id="cid")


class TestRestartContainer:
    def test_missing_raises(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        mock_client.containers.get.side_effect = docker.errors.NotFound("nope")

        with pytest.raises(ContainerNotFoundError, match="gone"):
            adapter.restart_container(container_id="gone")

    def test_restart_then_running_success(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(status="running", pid=1)

        def restart_side_effect(*_a, **kw) -> None:
            ctr.attrs = _sample_attrs(status="running", pid=2)

        ctr.restart.side_effect = restart_side_effect
        mock_client.containers.get.return_value = ctr

        r = adapter.restart_container(container_id="cid")

        ctr.restart.assert_called_once_with(timeout=10)
        assert r.success is True
        assert r.container_state == "running"

    def test_restart_api_error_raises_start_error(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(status="running", pid=1)
        ctr.restart.side_effect = docker.errors.APIError("restart boom")
        mock_client.containers.get.return_value = ctr

        with pytest.raises(ContainerStartError, match="restart boom"):
            adapter.restart_container(container_id="cid")

    def test_restart_success_false_when_not_running_after(
        self, adapter: DockerRuntimeAdapter, mock_client: MagicMock
    ) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(status="running", pid=1)

        def restart_side_effect(*_a, **kw) -> None:
            ctr.attrs = _sample_attrs(status="exited", pid=0)

        ctr.restart.side_effect = restart_side_effect
        mock_client.containers.get.return_value = ctr

        r = adapter.restart_container(container_id="cid")

        assert r.success is False
        assert r.container_state == "exited"
        assert r.message is not None and "unexpected state" in r.message


class TestDeleteContainer:
    def test_missing_returns_idempotent_success(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        mock_client.containers.get.side_effect = docker.errors.NotFound("nope")

        r = adapter.delete_container(container_id="gone")

        assert r.success is True
        assert r.container_state == "missing"
        assert r.container_id == "gone"

    def test_exited_removes_without_stop(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(status="exited", pid=0)
        n = 0

        def get_side_effect(cid: str, *a, **kw):
            nonlocal n
            n += 1
            if n <= 2:
                return ctr
            raise docker.errors.NotFound("gone")

        mock_client.containers.get.side_effect = get_side_effect

        r = adapter.delete_container(container_id="cid")

        ctr.stop.assert_not_called()
        ctr.remove.assert_called_once_with()
        assert r.success is True
        assert r.container_state == "missing"

    def test_running_stops_then_removes(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(status="running", pid=1)
        n = 0

        def get_side_effect(cid: str, *a, **kw):
            nonlocal n
            n += 1
            if n <= 2:
                return ctr
            raise docker.errors.NotFound("gone")

        mock_client.containers.get.side_effect = get_side_effect

        r = adapter.delete_container(container_id="cid")

        ctr.stop.assert_called_once_with(timeout=10)
        ctr.remove.assert_called_once_with()
        assert r.success is True
        assert r.container_state == "missing"

    def test_stop_failure_raises(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(status="running", pid=1)
        ctr.stop.side_effect = docker.errors.APIError("stop nope")
        mock_client.containers.get.return_value = ctr

        with pytest.raises(ContainerStopError, match="stop nope"):
            adapter.delete_container(container_id="cid")

        ctr.remove.assert_not_called()

    def test_remove_failure_raises_delete_error(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        ctr.attrs = _sample_attrs(status="exited", pid=0)
        ctr.remove.side_effect = docker.errors.APIError("remove nope")
        mock_client.containers.get.return_value = ctr

        with pytest.raises(ContainerDeleteError, match="remove nope"):
            adapter.delete_container(container_id="cid")


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

    def test_non_positive_pid_raises(self, adapter: DockerRuntimeAdapter, mock_client: MagicMock) -> None:
        ctr = MagicMock()
        attrs = _sample_attrs(pid=1, status="running")
        attrs["State"]["Pid"] = -1
        ctr.attrs = attrs
        mock_client.containers.get.return_value = ctr

        with pytest.raises(NetnsRefError, match="no host PID"):
            adapter.get_container_netns_ref(container_id="c1")

    def test_raises_when_inspect_exists_but_container_id_missing(
        self, adapter: DockerRuntimeAdapter, mock_client: MagicMock
    ) -> None:
        ctr = MagicMock()
        attrs = _sample_attrs(cid="", pid=100, status="running")
        attrs["Id"] = ""
        ctr.attrs = attrs
        mock_client.containers.get.return_value = ctr

        with pytest.raises(NetnsRefError, match="not found"):
            adapter.get_container_netns_ref(container_id="by-name")


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
