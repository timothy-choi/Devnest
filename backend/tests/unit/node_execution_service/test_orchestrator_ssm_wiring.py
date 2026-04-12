"""Orchestrator + SSM bundle wiring without PostgreSQL (avoids ``tests/integration`` session fixtures)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.libs.runtime.ssm_docker_runtime import SsmDockerRuntimeAdapter
from app.services.orchestrator_service.app_factory import build_default_orchestrator_for_session
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


@patch("app.services.node_execution_service.factory.SsmRemoteCommandRunner")
def test_orchestrator_factory_ssm_docker_end_to_end(mock_runner_cls, ne_engine) -> None:
    runner_inst = MagicMock()
    runner_inst.run.return_value = ""
    mock_runner_cls.return_value = runner_inst
    with Session(ne_engine) as session:
        session.add(
            ExecutionNode(
                node_key="int-ssm-node",
                name="int-ssm-node",
                provider_type=ExecutionNodeProviderType.EC2.value,
                status=ExecutionNodeStatus.READY.value,
                schedulable=True,
                execution_mode=ExecutionNodeExecutionMode.SSM_DOCKER.value,
                provider_instance_id="i-0integration000001",
                region="us-east-1",
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
        settings.workspace_container_image = "nginx:alpine"
        settings.workspace_projects_base = "/var/devnest"
        with patch("app.services.orchestrator_service.app_factory.get_settings", return_value=settings):
            with patch(
                "app.services.node_execution_service.factory.get_settings",
                return_value=settings,
            ):
                orch = build_default_orchestrator_for_session(
                    session,
                    execution_node_key="int-ssm-node",
                    topology_id=9,
                )
    assert isinstance(orch._runtime_adapter, SsmDockerRuntimeAdapter)
    assert orch._runtime_adapter is orch._probe_runner._runtime
