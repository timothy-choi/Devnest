"""Unit tests: SSM ``send_run_shell_script`` with botocore Stubber (no live AWS)."""

from __future__ import annotations

import pytest
import botocore.session
from botocore.stub import ANY, Stubber

from app.services.node_execution_service.errors import SsmExecutionError
from app.services.node_execution_service.ssm_send_command import send_run_shell_script


@pytest.fixture
def ssm_stubbed_client():
    session = botocore.session.get_session()
    client = session.create_client("ssm", region_name="us-east-1")
    with Stubber(client) as stubber:
        yield client, stubber


def test_send_run_shell_script_success(ssm_stubbed_client) -> None:
    client, stubber = ssm_stubbed_client
    iid = "i-0123456789abcdef0"
    cmd_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    stubber.add_response(
        "send_command",
        {"Command": {"CommandId": cmd_id}},
        {
            "InstanceIds": [iid],
            "DocumentName": "AWS-RunShellScript",
            "Comment": ANY,
            "TimeoutSeconds": ANY,
            "Parameters": {"commands": ["echo hello"]},
        },
    )
    stubber.add_response(
        "get_command_invocation",
        {
            "Status": "Success",
            "StandardOutputContent": "hello\n",
            "StandardErrorContent": "",
        },
        {"CommandId": cmd_id, "InstanceId": iid},
    )
    out, err = send_run_shell_script(client, iid, ["echo hello"], comment="DevNest")
    assert "hello" in out
    assert err == ""


def test_send_run_shell_script_polls_then_success(ssm_stubbed_client) -> None:
    client, stubber = ssm_stubbed_client
    iid = "i-0poll000000000000"
    cmd_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    stubber.add_response(
        "send_command",
        {"Command": {"CommandId": cmd_id}},
        {
            "InstanceIds": [iid],
            "DocumentName": "AWS-RunShellScript",
            "Comment": ANY,
            "TimeoutSeconds": ANY,
            "Parameters": {"commands": ["true"]},
        },
    )
    stubber.add_response(
        "get_command_invocation",
        {"Status": "InProgress", "StandardOutputContent": "", "StandardErrorContent": ""},
        {"CommandId": cmd_id, "InstanceId": iid},
    )
    stubber.add_response(
        "get_command_invocation",
        {
            "Status": "Success",
            "StandardOutputContent": "done",
            "StandardErrorContent": "",
        },
        {"CommandId": cmd_id, "InstanceId": iid},
    )
    out, _ = send_run_shell_script(client, iid, ["true"])
    assert "done" in out


def test_send_run_shell_script_non_success_raises(ssm_stubbed_client) -> None:
    client, stubber = ssm_stubbed_client
    iid = "i-0fail000000000000"
    cmd_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    stubber.add_response(
        "send_command",
        {"Command": {"CommandId": cmd_id}},
        {
            "InstanceIds": [iid],
            "DocumentName": "AWS-RunShellScript",
            "Comment": ANY,
            "TimeoutSeconds": ANY,
            "Parameters": {"commands": ["false"]},
        },
    )
    stubber.add_response(
        "get_command_invocation",
        {
            "Status": "Failed",
            "StandardOutputContent": "",
            "StandardErrorContent": "command failed",
        },
        {"CommandId": cmd_id, "InstanceId": iid},
    )
    with pytest.raises(SsmExecutionError, match="Failed"):
        send_run_shell_script(client, iid, ["false"])
