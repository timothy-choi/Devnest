"""Cross-component system test: real ``DockerRuntimeAdapter`` + ``DbTopologyAdapter`` + ``DefaultProbeRunner``.

Exercises the same shape of flow as production (without the orchestrator): runtime creates a
running workspace-like container listening on ``WORKSPACE_IDE_CONTAINER_PORT``, topology wires
bridge + veth + netns, then the probe runner rolls up container + topology + TCP reachability.

Requires Linux + CAP_NET_ADMIN + Docker (``topology_linux``). See ``README.md`` in this folder.

Uses ``nginx:alpine`` explicitly (bind-mounted ``nginx.conf`` listening on ``WORKSPACE_IDE_CONTAINER_PORT``)
so the service probe matches production’s in-container IDE port without pulling the full workspace image.
"""

from __future__ import annotations

import shutil
import socket
import tempfile
import time
import uuid
from pathlib import Path

import pytest
from sqlmodel import Session

from app.libs.probes import DefaultProbeRunner
from app.libs.runtime.docker_runtime import DockerRuntimeAdapter
from app.libs.runtime.models import WORKSPACE_IDE_CONTAINER_PORT, WorkspaceExtraBindMountSpec

from tests.system.conftest import _remove_container_force
from tests.system.topology.test_topology_v1_linux import (
    _force_cleanup_node_bridge,
    _unique_ipv4_subnet,
    seed_topology,
)

pytestmark = [pytest.mark.system, pytest.mark.topology_linux]

_WORKSPACE_LISTENER_IMAGE = "nginx:alpine"


def _wait_tcp_connect(host: str, port: int, *, deadline_s: float = 20.0) -> None:
    """Poll until the workspace IP accepts TCP on ``port`` (nginx may need a moment after attach)."""
    ip = host.strip()
    t0 = time.monotonic()
    last_err: OSError | None = None
    while time.monotonic() - t0 < deadline_s:
        try:
            with socket.create_connection((ip, port), timeout=1.5):
                return
        except OSError as e:
            last_err = e
            time.sleep(0.2)
    msg = f"no TCP accept on {ip!r}:{port} within {deadline_s}s"
    if last_err is not None:
        msg = f"{msg}: {last_err}"
    pytest.fail(msg)


def test_runtime_ensure_topology_attach_probe_workspace_health_happy_path(
    linux_net_admin_or_skip: None,
    linux_topology_adapter,
    topology_sqlite_session: Session,
    docker_client,
) -> None:
    """
    1. Runtime: ensure + start container (``nginx:alpine`` on 8080 via bind-mounted config).
    2. Runtime: ``container_id`` + ``get_container_netns_ref`` for topology attach.
    3. Topology: ``ensure_node_topology`` → ``allocate_workspace_ip`` → ``attach_workspace``.
    4. Probe: ``check_workspace_health`` → healthy with ``internal_endpoint == workspace_ip:8080``.
    """
    cidr, gw = _unique_ipv4_subnet()
    bridge = f"b{uuid.uuid4().hex[:6]}"
    tid = seed_topology(
        topology_sqlite_session,
        spec={"cidr": cidr, "gateway_ip": gw, "bridge_name": bridge},
    )
    node_id = f"node-{uuid.uuid4().hex[:8]}"
    ws_id = 7000 + (uuid.uuid4().int % 2000)
    name = f"devnest-e2e-{uuid.uuid4().hex[:12]}"
    base = Path(tempfile.mkdtemp(prefix="devnest-rtp-"))
    runtime: DockerRuntimeAdapter | None = None
    ensured = None
    try:
        project = base / "project"
        project.mkdir(parents=True, exist_ok=True)
        nginx_conf = base / "nginx.conf"
        nginx_conf.write_text(
            "daemon off;\n"
            "pid /tmp/nginx-e2e.pid;\n"
            "events { worker_connections 16; }\n"
            "http {\n"
            "  server {\n"
            f"    listen {WORKSPACE_IDE_CONTAINER_PORT};\n"
            "    location / {\n"
            "      add_header Content-Type text/plain;\n"
            "      return 200 'ok\\n';\n"
            "    }\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )

        docker_client.images.pull(_WORKSPACE_LISTENER_IMAGE)

        runtime = DockerRuntimeAdapter(client=docker_client)
        ensured = runtime.ensure_container(
            name=name,
            image=_WORKSPACE_LISTENER_IMAGE,
            workspace_host_path=str(project),
            ports=((0, WORKSPACE_IDE_CONTAINER_PORT),),
            labels={"devnest.system_test": "runtime_topology_probe"},
            extra_bind_mounts=(
                WorkspaceExtraBindMountSpec(
                    host_path=str(nginx_conf),
                    container_path="/etc/nginx/nginx.conf",
                    read_only=True,
                ),
            ),
        )
        assert ensured.container_id
        started = runtime.start_container(container_id=ensured.container_id)
        assert started.success is True
        assert started.container_state == "running"

        netns = runtime.get_container_netns_ref(container_id=ensured.container_id)
        assert netns.netns_ref.startswith("/proc/")
        assert netns.netns_ref.endswith("/ns/net")

        linux_topology_adapter.ensure_node_topology(topology_id=tid, node_id=node_id)
        ip_res = linux_topology_adapter.allocate_workspace_ip(
            topology_id=tid,
            node_id=node_id,
            workspace_id=ws_id,
        )
        linux_topology_adapter.attach_workspace(
            topology_id=tid,
            node_id=node_id,
            workspace_id=ws_id,
            container_id=ensured.container_id,
            netns_ref=netns.netns_ref,
            workspace_ip=ip_res.workspace_ip,
        )

        _wait_tcp_connect(ip_res.workspace_ip, WORKSPACE_IDE_CONTAINER_PORT)

        runner = DefaultProbeRunner(runtime=runtime, topology=linux_topology_adapter)
        out = runner.check_workspace_health(
            workspace_id=str(ws_id),
            topology_id=str(tid),
            node_id=node_id,
            container_id=ensured.container_id,
            expected_port=WORKSPACE_IDE_CONTAINER_PORT,
            timeout_seconds=5.0,
        )

        assert out.healthy is True, out.issues
        assert out.runtime_healthy is True
        assert out.topology_healthy is True
        assert out.service_healthy is True
        assert out.workspace_id == ws_id
        assert out.workspace_ip == ip_res.workspace_ip
        assert out.internal_endpoint == f"{ip_res.workspace_ip}:{WORKSPACE_IDE_CONTAINER_PORT}"
        assert out.container_state == "running"
        assert out.issues == ()
    finally:
        try:
            linux_topology_adapter.detach_workspace(
                topology_id=tid,
                node_id=node_id,
                workspace_id=ws_id,
            )
        except Exception:
            pass
        try:
            _force_cleanup_node_bridge(linux_topology_adapter, tid, node_id, bridge)
        except Exception:
            pass
        if runtime is not None and ensured is not None and ensured.container_id:
            try:
                runtime.delete_container(container_id=ensured.container_id)
            except Exception:
                pass
        _remove_container_force(docker_client, name)
        shutil.rmtree(base, ignore_errors=True)
