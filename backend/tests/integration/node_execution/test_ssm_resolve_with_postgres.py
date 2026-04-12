"""Integration: SSM execution bundle resolves against real DB schema (runner mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session

from app.libs.runtime.ssm_docker_runtime import SsmDockerRuntimeAdapter
from app.services.node_execution_service.factory import resolve_node_execution_bundle
from app.services.placement_service.models import (
    ExecutionNode,
    ExecutionNodeExecutionMode,
    ExecutionNodeProviderType,
    ExecutionNodeStatus,
)

pytestmark = pytest.mark.integration


@patch("app.services.node_execution_service.factory.SsmRemoteCommandRunner")
def test_resolve_ssm_docker_node_from_database(mock_runner_cls, db_session: Session) -> None:
    mock_r = MagicMock()
    mock_r.run.return_value = ""
    mock_runner_cls.return_value = mock_r

    db_session.add(
        ExecutionNode(
            node_key="int-ssm-ec2",
            name="int-ssm-ec2",
            provider_type=ExecutionNodeProviderType.EC2.value,
            provider_instance_id="i-0integrationssm0001",
            region="us-east-1",
            execution_mode=ExecutionNodeExecutionMode.SSM_DOCKER.value,
            status=ExecutionNodeStatus.READY.value,
            schedulable=True,
            total_cpu=4.0,
            total_memory_mb=8192,
            allocatable_cpu=4.0,
            allocatable_memory_mb=8192,
        ),
    )
    db_session.commit()

    bundle = resolve_node_execution_bundle(db_session, "int-ssm-ec2")
    assert bundle.docker_client is None
    assert isinstance(bundle.runtime_adapter, SsmDockerRuntimeAdapter)
    mock_runner_cls.assert_called_once_with(instance_id="i-0integrationssm0001", region="us-east-1")
