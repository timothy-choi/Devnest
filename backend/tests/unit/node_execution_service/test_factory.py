"""Unit tests: :func:`resolve_node_execution_bundle` (mocked Docker)."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.services.node_execution_service import NodeExecutionBackend, NodeExecutionBundle
from app.services.node_execution_service.errors import NodeExecutionBindingError
from app.services.node_execution_service.factory import resolve_node_execution_bundle
from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
    ExecutionNodeStatus,
)


@pytest.fixture
def ne_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_empty_node_key_uses_local_bundle_without_db(ne_engine) -> None:
    mock_client = MagicMock()
    with Session(ne_engine) as session, patch(
        "app.services.node_execution_service.factory.docker.from_env",
        return_value=mock_client,
    ):
        bundle = resolve_node_execution_bundle(session, "  ")
    mock_client.ping.assert_called()
    assert bundle.service_reachability_runner is None
    assert isinstance(bundle, NodeExecutionBackend)
    assert isinstance(bundle, NodeExecutionBundle)


def test_unknown_node_key_raises(ne_engine) -> None:
    with Session(ne_engine) as session, patch(
        "app.services.node_execution_service.factory.docker.from_env",
        return_value=MagicMock(),
    ):
        with pytest.raises(NodeExecutionBindingError, match="no execution_node row"):
            resolve_node_execution_bundle(session, "missing-key")


def test_local_docker_row_uses_local_bundle(ne_engine) -> None:
    mock_client = MagicMock()
    with Session(ne_engine) as session:
        session.add(
            ExecutionNode(
                node_key="n1",
                name="n1",
                provider_type=ExecutionNodeProviderType.LOCAL.value,
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                execution_mode=ExecutionNodeExecutionMode.LOCAL_DOCKER.value,
                total_cpu=4.0,
                total_memory_mb=8192,
                allocatable_cpu=4.0,
                allocatable_memory_mb=8192,
            ),
        )
        session.commit()
        with patch(
            "app.services.node_execution_service.factory.docker.from_env",
            return_value=mock_client,
        ):
            bundle = resolve_node_execution_bundle(session, "n1")
    assert bundle.topology_command_runner is not None
    assert bundle.service_reachability_runner is None


def test_unsupported_execution_mode_raises(ne_engine) -> None:
    with Session(ne_engine) as session:
        session.add(
            ExecutionNode(
                node_key="bad-mode",
                name="bad-mode",
                provider_type=ExecutionNodeProviderType.LOCAL.value,
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                execution_mode="ssm_session",  # not implemented in V1
                total_cpu=4.0,
                total_memory_mb=8192,
                allocatable_cpu=4.0,
                allocatable_memory_mb=8192,
            ),
        )
        session.commit()
        with patch(
            "app.services.node_execution_service.factory.docker.from_env",
            return_value=MagicMock(),
        ):
            with pytest.raises(NodeExecutionBindingError, match="unsupported execution_mode"):
                resolve_node_execution_bundle(session, "bad-mode")


def test_ssh_docker_requires_host(ne_engine) -> None:
    with Session(ne_engine) as session:
        session.add(
            ExecutionNode(
                node_key="remote1",
                name="remote1",
                provider_type=ExecutionNodeProviderType.EC2.value,
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                execution_mode=ExecutionNodeExecutionMode.SSH_DOCKER.value,
                ssh_host=None,
                hostname=None,
                total_cpu=4.0,
                total_memory_mb=8192,
                allocatable_cpu=4.0,
                allocatable_memory_mb=8192,
            ),
        )
        session.commit()
        with pytest.raises(NodeExecutionBindingError, match="requires ssh_host"):
            resolve_node_execution_bundle(session, "remote1")


@patch("app.services.node_execution_service.factory.docker.DockerClient")
def test_ssh_docker_uses_ssh_url(mock_docker_client_cls, ne_engine) -> None:
    mock_client = MagicMock()
    mock_docker_client_cls.return_value = mock_client
    with patch.dict(sys.modules, {"paramiko": ModuleType("paramiko")}):
        with Session(ne_engine) as session:
            session.add(
                ExecutionNode(
                    node_key="r2",
                    name="r2",
                    provider_type=ExecutionNodeProviderType.EC2.value,
                    status=ExecutionNodeStatus.READY.value,
                    schedulable=True,
                    execution_mode=ExecutionNodeExecutionMode.SSH_DOCKER.value,
                    ssh_host="10.0.0.5",
                    ssh_user="ubuntu",
                    ssh_port=22,
                    total_cpu=4.0,
                    total_memory_mb=8192,
                    allocatable_cpu=4.0,
                    allocatable_memory_mb=8192,
                ),
            )
            session.commit()
            with patch("app.services.node_execution_service.factory.docker.from_env", MagicMock()):
                bundle = resolve_node_execution_bundle(session, "r2")
            row = session.exec(select(ExecutionNode).where(ExecutionNode.node_key == "r2")).first()
            assert row is not None

    mock_docker_client_cls.assert_called_once()
    call_kw = mock_docker_client_cls.call_args.kwargs
    assert call_kw["base_url"].startswith("ssh://")
    assert "10.0.0.5" in call_kw["base_url"]
    mock_client.ping.assert_called_once()
    assert bundle.service_reachability_runner is not None
