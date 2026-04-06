"""
System tests: ``DockerRuntimeAdapter`` against the real built workspace image (code-server stack).

Uses the same ``built_workspace_image`` session fixture as other workspace system tests.
Slower than ``nginx:alpine`` lifecycle tests in ``tests/system/runtime/``; run with the
``workspace_image`` marker. Validates ephemeral host publish, mounts, and **code-server HTTP**
response on the assigned host port (no fixed host 8080 requirement).
"""

from __future__ import annotations

import os
import shutil
import uuid

import pytest

from app.libs.runtime.docker_runtime import DockerRuntimeAdapter
from app.libs.runtime.models import (
    CODE_SERVER_CONFIG_CONTAINER_PATH,
    CODE_SERVER_DATA_CONTAINER_PATH,
    WORKSPACE_IDE_CONTAINER_PORT,
    WORKSPACE_PROJECT_CONTAINER_PATH,
    WorkspaceExtraBindMountSpec,
)

from tests.system.conftest import _remove_container_force
from tests.system.workspace.test_workspace_image_system import _wait_for_code_server_http

pytestmark = [pytest.mark.system, pytest.mark.workspace_image]

_WORKSPACE_HTTP_TIMEOUT_S = float(os.environ.get("DEVNEST_WORKSPACE_TEST_STARTUP_TIMEOUT", "240"))


def test_adapter_workspace_image_ephemeral_port_project_and_code_server_mounts_persist(
    docker_client,
    built_workspace_image: str,
) -> None:
    name = f"devnest-rta-ws-{uuid.uuid4().hex[:12]}"
    tmp_root = os.environ.get("TMPDIR", "/tmp")
    workspace = os.path.join(tmp_root, f"devnest-rta-proj-{uuid.uuid4().hex[:12]}")
    cfg_h = os.path.join(tmp_root, f"devnest-rta-cfg-{uuid.uuid4().hex[:10]}")
    data_h = os.path.join(tmp_root, f"devnest-rta-dat-{uuid.uuid4().hex[:10]}")
    os.makedirs(workspace, mode=0o755, exist_ok=False)
    os.makedirs(cfg_h, mode=0o755, exist_ok=False)
    os.makedirs(data_h, mode=0o755, exist_ok=False)

    adapter = DockerRuntimeAdapter(client=docker_client)
    try:
        ensured = adapter.ensure_container(
            name=name,
            image=built_workspace_image,
            workspace_host_path=workspace,
            ports=((0, WORKSPACE_IDE_CONTAINER_PORT),),
            labels={"devnest.system_test": "runtime-workspace-image"},
            extra_bind_mounts=(
                WorkspaceExtraBindMountSpec(host_path=cfg_h, container_path=CODE_SERVER_CONFIG_CONTAINER_PATH),
                WorkspaceExtraBindMountSpec(host_path=data_h, container_path=CODE_SERVER_DATA_CONTAINER_PATH),
            ),
        )
        assert ensured.created_new is True
        adapter.start_container(container_id=ensured.container_id)

        ins = adapter.inspect_container(container_id=ensured.container_id)
        assert ins.ports
        host_port, container_port = ins.ports[0]
        assert container_port == WORKSPACE_IDE_CONTAINER_PORT
        assert isinstance(host_port, int) and host_port > 0

        status, _body = _wait_for_code_server_http(
            "127.0.0.1",
            host_port,
            timeout_s=_WORKSPACE_HTTP_TIMEOUT_S,
        )
        assert status in (200, 301, 302, 303, 307, 308, 401, 403)

        ctr = docker_client.containers.get(ensured.container_id)
        # Image runs as ``coder``; host bind mounts are owned by the test user/CI UID, so default
        # exec would get permission denied. Root can write; we only assert files appear on host.
        code, out = ctr.exec_run(
            f"sh -c 'echo ws-mark > {WORKSPACE_PROJECT_CONTAINER_PATH}/file.txt "
            f"&& mkdir -p {CODE_SERVER_CONFIG_CONTAINER_PATH} {CODE_SERVER_DATA_CONTAINER_PATH} "
            f"&& echo cfg > {CODE_SERVER_CONFIG_CONTAINER_PATH}/c.json "
            f"&& echo dat > {CODE_SERVER_DATA_CONTAINER_PATH}/d.txt'",
            demux=False,
            user="root",
        )
        assert code == 0, out.decode("utf-8", errors="replace")

        adapter.stop_container(container_id=ensured.container_id)

        with open(os.path.join(workspace, "file.txt"), encoding="utf-8") as f:
            assert f.read().strip() == "ws-mark"
        with open(os.path.join(cfg_h, "c.json"), encoding="utf-8") as f:
            assert f.read().strip() == "cfg"
        with open(os.path.join(data_h, "d.txt"), encoding="utf-8") as f:
            assert f.read().strip() == "dat"
    finally:
        _remove_container_force(docker_client, name)
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(cfg_h, ignore_errors=True)
        shutil.rmtree(data_h, ignore_errors=True)
