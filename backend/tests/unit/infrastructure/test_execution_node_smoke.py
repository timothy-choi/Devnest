"""Unit tests: execution node read-only SSM/SSH smoke (Phase 3b Step 6)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.services.infrastructure_service.execution_node_smoke import (
    ExecutionNodeSmokeUnsupportedError,
    run_read_only_execution_node_smoke,
)
from app.services.placement_service.bootstrap import default_local_node_key, ensure_default_local_execution_node
from app.services.placement_service.models import ExecutionNode, ExecutionNodeExecutionMode, ExecutionNodeProviderType


def test_smoke_unsupported_for_local_provider(infrastructure_unit_engine: Engine) -> None:
    with Session(infrastructure_unit_engine) as session:
        key = default_local_node_key()
        ensure_default_local_execution_node(session)
        session.commit()
        with pytest.raises(ExecutionNodeSmokeUnsupportedError, match="provider_type=ec2"):
            run_read_only_execution_node_smoke(session, node_key=key)


def test_smoke_ssm_ec2_success(infrastructure_unit_engine: Engine) -> None:
    with Session(infrastructure_unit_engine) as session:
        row = ExecutionNode(
            node_key="unit-smoke-ssm-node",
            name="unit-smoke-ssm-node",
            provider_type=ExecutionNodeProviderType.EC2.value,
            provider_instance_id="i-0123456789abcdef0",
            region="us-east-1",
            private_ip="10.0.1.50",
            execution_mode=ExecutionNodeExecutionMode.SSM_DOCKER.value,
            schedulable=False,
        )
        session.add(row)
        session.commit()
        session.refresh(row)

    with Session(infrastructure_unit_engine) as session:
        with patch(
            "app.services.infrastructure_service.execution_node_smoke.send_run_shell_script",
            return_value=("Server:\n  Docker\n", ""),
        ) as send:
            out = run_read_only_execution_node_smoke(session, node_key="unit-smoke-ssm-node")
        send.assert_called_once()
        assert out["ok"] is True
        assert out["node_key"] == "unit-smoke-ssm-node"
        assert out["command_status"] == "Success"
        assert out["schedulable"] is False
        assert "Docker" in out["output_preview"]


def test_smoke_ssm_returns_failure_payload_on_ssm_error(infrastructure_unit_engine: Engine) -> None:
    from app.services.node_execution_service.errors import SsmExecutionError

    with Session(infrastructure_unit_engine) as session:
        row = ExecutionNode(
            node_key="unit-smoke-ssm-fail",
            name="unit-smoke-ssm-fail",
            provider_type=ExecutionNodeProviderType.EC2.value,
            provider_instance_id="i-0fedcba9876543210",
            region="us-east-1",
            execution_mode=ExecutionNodeExecutionMode.SSM_DOCKER.value,
            schedulable=False,
        )
        session.add(row)
        session.commit()

    with Session(infrastructure_unit_engine) as session:
        with patch(
            "app.services.infrastructure_service.execution_node_smoke.send_run_shell_script",
            side_effect=SsmExecutionError("Throttled"),
        ):
            out = run_read_only_execution_node_smoke(session, node_key="unit-smoke-ssm-fail")
    assert out["ok"] is False
    assert out["command_status"] == "Failed"
    assert "Throttled" in out["output_preview"]


def test_smoke_ssh_ec2_success(infrastructure_unit_engine: Engine) -> None:
    with Session(infrastructure_unit_engine) as session:
        row = ExecutionNode(
            node_key="unit-smoke-ssh-node",
            name="unit-smoke-ssh-node",
            provider_type=ExecutionNodeProviderType.EC2.value,
            provider_instance_id="i-0aaaaaaaaaaaaaaa1",
            region="us-west-2",
            private_ip="10.1.1.10",
            execution_mode=ExecutionNodeExecutionMode.SSH_DOCKER.value,
            ssh_user="ubuntu",
            ssh_port=22,
            schedulable=False,
        )
        session.add(row)
        session.commit()

    mock_runner = MagicMock()
    mock_runner.run.return_value = "Docker version 24.0\n"
    with Session(infrastructure_unit_engine) as session:
        with patch(
            "app.services.infrastructure_service.execution_node_smoke.SshRemoteCommandRunner",
            return_value=mock_runner,
        ):
            out = run_read_only_execution_node_smoke(session, node_key="unit-smoke-ssh-node")
    mock_runner.run.assert_called_once()
    assert out["ok"] is True
    assert out["execution_mode"] == ExecutionNodeExecutionMode.SSH_DOCKER.value
    assert "Docker" in out["output_preview"]


def test_smoke_ssh_missing_host_skipped(infrastructure_unit_engine: Engine) -> None:
    with Session(infrastructure_unit_engine) as session:
        row = ExecutionNode(
            node_key="unit-smoke-ssh-nohost",
            name="unit-smoke-ssh-nohost",
            provider_type=ExecutionNodeProviderType.EC2.value,
            provider_instance_id="i-0bbbbbbbbbbbbbbb2",
            region="us-west-2",
            private_ip="",
            hostname="",
            ssh_host="",
            execution_mode=ExecutionNodeExecutionMode.SSH_DOCKER.value,
            schedulable=False,
        )
        session.add(row)
        session.commit()

    with Session(infrastructure_unit_engine) as session:
        out = run_read_only_execution_node_smoke(session, node_key="unit-smoke-ssh-nohost")
    assert out["ok"] is False
    assert out["command_status"] == "Skipped"
    assert "private_ip" in out["output_preview"].lower() or "ssh" in out["output_preview"].lower()


def test_smoke_api_route_registered() -> None:
    from app.services.infrastructure_service.api.routers import internal_execution_nodes_router

    paths = [getattr(r, "path", "") for r in internal_execution_nodes_router.routes]
    assert "/internal/execution-nodes/smoke-read-only" in paths
