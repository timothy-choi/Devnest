"""Fixtures for workspace integration tests that need Docker (control-plane E2E).

Cannot use ``pytest_plugins`` to pull in ``tests.system.conftest`` from a nested conftest
(Pytest 8+ forbids non-root ``pytest_plugins``). This module duplicates only the small
``docker_client`` session helper; PostgreSQL + ``TestClient`` come from parent
``tests/integration/conftest.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import docker
import pytest
from sqlmodel import Session

from app.libs.topology.models import Topology


@pytest.fixture(scope="session")
def docker_client() -> Generator[docker.DockerClient, None, None]:
    """Real Docker engine (same contract as ``tests/system/conftest.py``)."""
    try:
        client = docker.from_env()
        client.ping()
    except Exception as e:
        pytest.fail(f"Docker daemon required for workspace control-plane E2E tests: {e}")
    yield client
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


@pytest.fixture
def _workspace_control_plane_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Orchestrator-friendly defaults: no host bridge/veth, lightweight workspace image."""
    monkeypatch.setenv("DEVNEST_TOPOLOGY_SKIP_LINUX_BRIDGE", "1")
    monkeypatch.setenv("DEVNEST_TOPOLOGY_SKIP_LINUX_ATTACHMENT", "1")
    monkeypatch.setenv("WORKSPACE_CONTAINER_IMAGE", "nginx:alpine")
    # Hermetic placement / execution for parallel CI (xdist): avoid stale env forcing ec2-only or SSM.
    monkeypatch.setenv("DEVNEST_NODE_PROVIDER", "all")
    monkeypatch.delenv("DEVNEST_EXECUTION_MODE", raising=False)
    from app.libs.common.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def orchestrator_topology(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> int:
    """Persist ``Topology`` and set ``DEVNEST_TOPOLOGY_ID`` for ``DbTopologyAdapter``."""
    oct2 = (uuid.uuid4().int % 200) + 1
    cidr = f"10.{oct2}.0.0/24"
    gateway = f"10.{oct2}.0.1"
    t = Topology(
        name=f"int-cp-{uuid.uuid4().hex[:8]}",
        version="v1",
        spec_json={
            "cidr": cidr,
            "gateway_ip": gateway,
            "bridge_name": f"brcp{oct2 % 900 + 100}"[:15],
        },
    )
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    assert t.topology_id is not None
    monkeypatch.setenv("DEVNEST_TOPOLOGY_ID", str(t.topology_id))
    return t.topology_id


@pytest.fixture
def e2e_probe_socket_patch() -> Generator[None, None, None]:
    """
    Stub TCP connect and HTTP probe for service probes (workspace IP not host-routable).

    Patches both ``_probe_create_connection`` (TCP) and ``_probe_urlopen`` (HTTP) so that
    ``check_workspace_health`` considers the service reachable without a live container.
    """

    class _FakeSock:
        def close(self) -> None:
            pass

    fake_http = MagicMock()
    fake_http.status = 200
    fake_http.__enter__ = lambda s: s
    fake_http.__exit__ = MagicMock(return_value=False)

    with patch(
        "app.libs.probes.probe_runner._probe_create_connection",
        return_value=_FakeSock(),
    ), patch(
        "app.libs.probes.probe_runner._probe_urlopen",
        return_value=fake_http,
    ):
        yield
