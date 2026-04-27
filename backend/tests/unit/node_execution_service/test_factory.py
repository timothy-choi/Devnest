"""Unit tests: :func:`resolve_node_execution_bundle` (mocked Docker)."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.libs.runtime.ssm_docker_runtime import SsmDockerRuntimeAdapter
from app.libs.topology.system.host_nsenter_command_runner import HostPid1NsenterProbeRunner
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
    ), patch(
        "app.services.node_execution_service.factory._topology_ip_should_use_host_nsenter",
        return_value=False,
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
        ), patch(
            "app.services.node_execution_service.factory._topology_ip_should_use_host_nsenter",
            return_value=False,
        ):
            bundle = resolve_node_execution_bundle(session, "n1")
    assert bundle.topology_command_runner is not None
    assert bundle.service_reachability_runner is None


def test_local_docker_sets_host_probe_runner_when_topology_uses_host_nsenter(ne_engine) -> None:
    mock_client = MagicMock()
    with Session(ne_engine) as session:
        session.add(
            ExecutionNode(
                node_key="n2",
                name="n2",
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
        ), patch(
            "app.services.node_execution_service.factory._topology_ip_should_use_host_nsenter",
            return_value=True,
        ):
            bundle = resolve_node_execution_bundle(session, "n2")
    assert isinstance(bundle.service_reachability_runner, HostPid1NsenterProbeRunner)


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
        with pytest.raises(NodeExecutionBindingError, match="ssh_host, hostname, or private_ip"):
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
    assert bundle.defer_topology_attach is True


@patch("app.services.node_execution_service.factory.docker.DockerClient")
def test_ssh_docker_prefers_ssh_host_over_private_ip(mock_docker_client_cls, ne_engine) -> None:
    mock_client = MagicMock()
    mock_docker_client_cls.return_value = mock_client
    with patch.dict(sys.modules, {"paramiko": ModuleType("paramiko")}):
        with Session(ne_engine) as session:
            session.add(
                ExecutionNode(
                    node_key="r-prefer",
                    name="r-prefer",
                    provider_type=ExecutionNodeProviderType.EC2.value,
                    status=ExecutionNodeStatus.READY.value,
                    schedulable=True,
                    execution_mode=ExecutionNodeExecutionMode.SSH_DOCKER.value,
                    ssh_host="10.0.0.1",
                    private_ip="10.0.0.2",
                    total_cpu=4.0,
                    total_memory_mb=8192,
                    allocatable_cpu=4.0,
                    allocatable_memory_mb=8192,
                ),
            )
            session.commit()
            with patch("app.services.node_execution_service.factory.docker.from_env", MagicMock()):
                resolve_node_execution_bundle(session, "r-prefer")
    base_url = mock_docker_client_cls.call_args.kwargs["base_url"]
    assert "10.0.0.1" in base_url
    assert "10.0.0.2" not in base_url


@patch("app.services.node_execution_service.factory.docker.DockerClient")
def test_ssh_docker_falls_back_to_private_ip(mock_docker_client_cls, ne_engine) -> None:
    mock_client = MagicMock()
    mock_docker_client_cls.return_value = mock_client
    with patch.dict(sys.modules, {"paramiko": ModuleType("paramiko")}):
        with Session(ne_engine) as session:
            session.add(
                ExecutionNode(
                    node_key="r-priv",
                    name="r-priv",
                    provider_type=ExecutionNodeProviderType.EC2.value,
                    status=ExecutionNodeStatus.READY.value,
                    schedulable=True,
                    execution_mode=ExecutionNodeExecutionMode.SSH_DOCKER.value,
                    ssh_host=None,
                    hostname=None,
                    private_ip="172.31.12.34",
                    total_cpu=4.0,
                    total_memory_mb=8192,
                    allocatable_cpu=4.0,
                    allocatable_memory_mb=8192,
                ),
            )
            session.commit()
            with patch("app.services.node_execution_service.factory.docker.from_env", MagicMock()):
                resolve_node_execution_bundle(session, "r-priv")
    base_url = mock_docker_client_cls.call_args.kwargs["base_url"]
    assert "172.31.12.34" in base_url


def test_ssm_docker_requires_instance_id(ne_engine) -> None:
    with Session(ne_engine) as session:
        session.add(
            ExecutionNode(
                node_key="ssm-no-iid",
                name="ssm-no-iid",
                provider_type=ExecutionNodeProviderType.EC2.value,
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                execution_mode=ExecutionNodeExecutionMode.SSM_DOCKER.value,
                provider_instance_id=None,
                region="us-east-1",
                total_cpu=4.0,
                total_memory_mb=8192,
                allocatable_cpu=4.0,
                allocatable_memory_mb=8192,
            ),
        )
        session.commit()
        with pytest.raises(NodeExecutionBindingError, match="provider_instance_id"):
            resolve_node_execution_bundle(session, "ssm-no-iid")


def test_ssm_docker_requires_region(ne_engine) -> None:
    with Session(ne_engine) as session:
        session.add(
            ExecutionNode(
                node_key="ssm-no-region",
                name="ssm-no-region",
                provider_type=ExecutionNodeProviderType.EC2.value,
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                execution_mode=ExecutionNodeExecutionMode.SSM_DOCKER.value,
                provider_instance_id="i-0abc123",
                region=None,
                total_cpu=4.0,
                total_memory_mb=8192,
                allocatable_cpu=4.0,
                allocatable_memory_mb=8192,
            ),
        )
        session.commit()
    settings = MagicMock()
    settings.devnest_execution_mode = ""
    settings.aws_region = ""
    settings.devnest_ec2_ssh_user_default = "ubuntu"
    settings.aws_access_key_id = ""
    settings.aws_secret_access_key = ""
    with patch("app.services.node_execution_service.factory.get_settings", return_value=settings):
        with Session(ne_engine) as session:
            with pytest.raises(NodeExecutionBindingError, match="region"):
                resolve_node_execution_bundle(session, "ssm-no-region")


@patch("app.services.node_execution_service.factory.SsmRemoteCommandRunner")
def test_ssm_docker_bundle_uses_runtime_adapter(mock_runner_cls, ne_engine) -> None:
    runner_inst = MagicMock()
    runner_inst.run.return_value = ""
    mock_runner_cls.return_value = runner_inst
    with Session(ne_engine) as session:
        session.add(
            ExecutionNode(
                node_key="ssm-ok",
                name="ssm-ok",
                provider_type=ExecutionNodeProviderType.EC2.value,
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                execution_mode=ExecutionNodeExecutionMode.SSM_DOCKER.value,
                provider_instance_id="i-0ssmtest00000001",
                region="us-west-2",
                total_cpu=4.0,
                total_memory_mb=8192,
                allocatable_cpu=4.0,
                allocatable_memory_mb=8192,
            ),
        )
        session.commit()
        settings = MagicMock()
        settings.devnest_execution_mode = ""
        settings.aws_region = ""
        with patch("app.services.node_execution_service.factory.get_settings", return_value=settings):
            bundle = resolve_node_execution_bundle(session, "ssm-ok")
    assert bundle.docker_client is None
    assert isinstance(bundle.runtime_adapter, SsmDockerRuntimeAdapter)
    assert bundle.topology_command_runner is runner_inst
    assert bundle.defer_topology_attach is True
    runner_inst.run.assert_called()
    mock_runner_cls.assert_called_once_with(instance_id="i-0ssmtest00000001", region="us-west-2")


@patch("app.services.node_execution_service.factory.SsmRemoteCommandRunner")
def test_devnest_execution_mode_ssm_overrides_ssh_docker(mock_runner_cls, ne_engine) -> None:
    """``DEVNEST_EXECUTION_MODE=ssm`` uses SSM even when the row still says ``ssh_docker``."""
    runner_inst = MagicMock()
    runner_inst.run.return_value = ""
    mock_runner_cls.return_value = runner_inst
    with Session(ne_engine) as session:
        session.add(
            ExecutionNode(
                node_key="ec2-ssh-row",
                name="ec2-ssh-row",
                provider_type=ExecutionNodeProviderType.EC2.value,
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                execution_mode=ExecutionNodeExecutionMode.SSH_DOCKER.value,
                ssh_host="10.0.0.1",
                provider_instance_id="i-0override0000001",
                region="eu-west-1",
                total_cpu=4.0,
                total_memory_mb=8192,
                allocatable_cpu=4.0,
                allocatable_memory_mb=8192,
            ),
        )
        session.commit()
        settings = MagicMock()
        settings.devnest_execution_mode = "ssm"
        settings.aws_region = ""
        with patch("app.services.node_execution_service.factory.get_settings", return_value=settings):
            bundle = resolve_node_execution_bundle(session, "ec2-ssh-row")
    assert bundle.docker_client is None
    assert isinstance(bundle.runtime_adapter, SsmDockerRuntimeAdapter)
    assert bundle.defer_topology_attach is True
    mock_runner_cls.assert_called_once_with(instance_id="i-0override0000001", region="eu-west-1")


def test_devnest_execution_mode_local_on_ec2_raises(ne_engine) -> None:
    with Session(ne_engine) as session:
        session.add(
            ExecutionNode(
                node_key="ec2-local-override",
                name="ec2-local-override",
                provider_type=ExecutionNodeProviderType.EC2.value,
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                execution_mode=ExecutionNodeExecutionMode.SSM_DOCKER.value,
                provider_instance_id="i-0x",
                region="us-east-1",
                total_cpu=4.0,
                total_memory_mb=8192,
                allocatable_cpu=4.0,
                allocatable_memory_mb=8192,
            ),
        )
        session.commit()
    settings = MagicMock()
    settings.devnest_execution_mode = "local"
    with patch("app.services.node_execution_service.factory.get_settings", return_value=settings):
        with Session(ne_engine) as session:
            with pytest.raises(NodeExecutionBindingError, match="DEVNEST_EXECUTION_MODE=local"):
                resolve_node_execution_bundle(session, "ec2-local-override")
