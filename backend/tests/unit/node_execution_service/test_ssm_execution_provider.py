"""Unit tests: :class:`SsmExecutionProvider` (mocked ``send_run_shell_script``)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.node_execution_service.errors import SsmExecutionError
from app.services.node_execution_service.ssm_execution_provider import SsmExecutionProvider
from app.services.placement_service.models import ExecutionNode, ExecutionNodeProviderType


def _ec2_node(**kwargs) -> ExecutionNode:
    defaults = dict(
        node_key="n1",
        name="n1",
        provider_type=ExecutionNodeProviderType.EC2.value,
        provider_instance_id="i-0providertest0001",
        region="us-east-1",
    )
    defaults.update(kwargs)
    return ExecutionNode(**defaults)


def test_ssm_execution_provider_requires_instance_id() -> None:
    node = _ec2_node(provider_instance_id="")
    with pytest.raises(SsmExecutionError, match="provider_instance_id"):
        SsmExecutionProvider(node)


@patch("app.services.node_execution_service.ssm_execution_provider.send_run_shell_script")
def test_execute_command_delegates_to_send(mock_send) -> None:
    mock_send.return_value = ("out", "")
    node = _ec2_node()
    p = SsmExecutionProvider(node, ssm_client=MagicMock())
    out, err = p.execute_command("echo hi")
    assert out == "out"
    assert err == ""
    mock_send.assert_called_once()
    call_kw = mock_send.call_args
    assert call_kw[0][1] == "i-0providertest0001"
    assert call_kw[0][2] == ["echo hi"]


@patch("app.services.node_execution_service.ssm_execution_provider.send_run_shell_script")
def test_inspect_container_runs_docker_inspect(mock_send) -> None:
    mock_send.return_value = ('[{"Id":"abc"}]', "")
    node = _ec2_node()
    p = SsmExecutionProvider(node, ssm_client=MagicMock())
    j = p.inspect_container("myctr")
    assert "Id" in j
    cmds = mock_send.call_args[0][2]
    assert len(cmds) == 1
    assert "docker inspect" in cmds[0]
    assert "myctr" in cmds[0]


@patch("app.services.node_execution_service.ssm_execution_provider.send_run_shell_script")
def test_delete_container_uses_docker_rm_f(mock_send) -> None:
    mock_send.return_value = ("", "")
    node = _ec2_node()
    p = SsmExecutionProvider(node, ssm_client=MagicMock())
    p.delete_container("cid")
    cmd = mock_send.call_args[0][2][0]
    assert "docker" in cmd
    assert "rm" in cmd
    assert "-f" in cmd
    assert "cid" in cmd
