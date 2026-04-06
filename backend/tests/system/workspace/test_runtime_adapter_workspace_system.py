"""
System tests: ``DockerRuntimeAdapter`` against the real built workspace image (code-server stack).

Uses the same ``built_workspace_image`` session fixture as other workspace system tests.
Slower than ``nginx:alpine`` lifecycle tests in ``tests/system/runtime/``; run with the
``workspace_image`` marker. Validates ephemeral host publish (inspected), mounts, and **code-server
HTTP** from inside the container on ``WORKSPACE_IDE_CONTAINER_PORT`` (avoids flaky host→published-port
access in some CI networks).
"""

from __future__ import annotations

import os
import shutil
import time
import uuid

import docker.errors
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

pytestmark = [pytest.mark.system, pytest.mark.workspace_image]

_WORKSPACE_HTTP_TIMEOUT_S = float(os.environ.get("DEVNEST_WORKSPACE_TEST_STARTUP_TIMEOUT", "240"))
_WAIT_RUNNING_S = float(os.environ.get("DEVNEST_WORKSPACE_TEST_RUNNING_WAIT", "120"))


def _chmod_world_writable_tree(path: str) -> None:
    """Ensure bind-mount sources are writable by container ``coder`` (arbitrary host UID in CI)."""
    os.chmod(path, 0o777)
    try:
        for root, dirs, files in os.walk(path):
            os.chmod(root, 0o777)
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o777)
            for f in files:
                os.chmod(os.path.join(root, f), 0o777)
    except OSError:
        pass


def _wait_container_running_or_fail_with_logs(ctr, *, timeout_s: float) -> None:
    """``docker exec`` returns 409 if the container is not running; code-server may need a moment."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ctr.reload()
        if ctr.status == "running":
            return
        if ctr.status in ("exited", "dead"):
            logs = ctr.logs(tail=200).decode("utf-8", errors="replace")
            pytest.fail(
                f"workspace container exited before tests could exec (status={ctr.status!r}). "
                f"Common cause: bind mounts not writable by the image user. Logs:\n{logs}",
            )
        time.sleep(1)
    logs = ctr.logs(tail=200).decode("utf-8", errors="replace")
    pytest.fail(
        f"container not running after {timeout_s}s (status={ctr.status!r}). Logs:\n{logs}",
    )


def _wait_code_server_http_inside_container(ctr, *, ide_port: int, timeout_s: float) -> None:
    """
    Probe code-server over HTTP on ``127.0.0.1:<ide_port>`` *inside* the container.

    Host-side ``127.0.0.1:<published>`` can fail in some CI/sandbox or networking setups even when
    the process is healthy; the adapter applies port maps for the Docker network path, so
    in-container reachability matches production ``connect`` from other containers / mesh.
    """
    deadline = time.monotonic() + timeout_s
    last: str = ""
    while time.monotonic() < deadline:
        ctr.reload()
        if ctr.status != "running":
            logs = ctr.logs(tail=120).decode("utf-8", errors="replace")
            pytest.fail(
                f"container stopped during HTTP wait (status={ctr.status!r}). Logs:\n{logs}",
            )
        try:
            code, raw = ctr.exec_run(
                [
                    "sh",
                    "-c",
                    f"curl -sS --connect-timeout 3 --max-time 8 -o /dev/null -w '%{{http_code}}' "
                    f"http://127.0.0.1:{ide_port}/",
                ],
                demux=False,
                user="coder",
            )
        except docker.errors.APIError as e:
            expl = getattr(e, "explanation", None) or str(e)
            if e.status_code == 409 or "not running" in expl.lower():
                logs = ctr.logs(tail=120).decode("utf-8", errors="replace")
                pytest.fail(f"docker exec rejected (container not running?): {expl}\nLogs:\n{logs}")
            raise
        txt = raw.decode("utf-8", errors="replace").strip()
        if code == 0 and txt.isdigit():
            status = int(txt)
            if status in (200, 301, 302, 303, 307, 308, 401, 403):
                return
        last = f"exit={code}, out={txt!r}"
        time.sleep(2)
    pytest.fail(
        f"code-server did not return an expected HTTP status inside the container within {timeout_s}s "
        f"(tried http://127.0.0.1:{ide_port}/ as user coder; last: {last})",
    )


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
    # Host dirs are owned by the CI uid; ``coder`` in the image is often a different uid. Open perms
    # keep this test about bind persistence, not host ownership edge cases.
    _chmod_world_writable_tree(workspace)
    _chmod_world_writable_tree(cfg_h)
    _chmod_world_writable_tree(data_h)

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

        ctr = docker_client.containers.get(ensured.container_id)
        _wait_container_running_or_fail_with_logs(ctr, timeout_s=_WAIT_RUNNING_S)

        ins = adapter.inspect_container(container_id=ensured.container_id)
        assert ins.ports
        host_port, container_port = ins.ports[0]
        assert container_port == WORKSPACE_IDE_CONTAINER_PORT
        assert isinstance(host_port, int) and host_port > 0

        _wait_code_server_http_inside_container(
            ctr,
            ide_port=WORKSPACE_IDE_CONTAINER_PORT,
            timeout_s=_WORKSPACE_HTTP_TIMEOUT_S,
        )
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
