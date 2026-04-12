"""Unit tests: SSM ``send_run_shell_script`` with botocore Stubber (no live AWS)."""

from __future__ import annotations

import botocore.session
import pytest
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
    iid = "i-0a0a0a0a0a0a0a0a"
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
    iid = "i-0b0b0b0b0b0b0b0b"
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


def test_send_run_shell_script_rejects_invalid_instance_id(ssm_stubbed_client) -> None:
    client, _stubber = ssm_stubbed_client
    with pytest.raises(SsmExecutionError, match="invalid SSM target"):
        send_run_shell_script(client, "not-an-ec2-id", ["echo x"])


def test_send_run_shell_script_rejects_empty_commands(ssm_stubbed_client) -> None:
    client, _stubber = ssm_stubbed_client
    with pytest.raises(SsmExecutionError, match="empty"):
        send_run_shell_script(client, "i-0123456789abcdef0", [])


def test_send_run_shell_script_send_throttle_then_success(ssm_stubbed_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.node_execution_service.ssm_send_command.time.sleep",
        lambda *_a, **_k: None,
    )
    client, stubber = ssm_stubbed_client
    iid = "i-0c0c0c0c0c0c0c0c"
    cmd_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    common_send = {
        "InstanceIds": [iid],
        "DocumentName": "AWS-RunShellScript",
        "Comment": ANY,
        "TimeoutSeconds": ANY,
        "Parameters": {"commands": ["echo ok"]},
    }
    stubber.add_client_error("send_command", "ThrottlingException", expected_params=common_send)
    stubber.add_response("send_command", {"Command": {"CommandId": cmd_id}}, common_send)
    stubber.add_response(
        "get_command_invocation",
        {
            "Status": "Success",
            "StandardOutputContent": "ok\n",
            "StandardErrorContent": "",
        },
        {"CommandId": cmd_id, "InstanceId": iid},
    )
    out, err = send_run_shell_script(client, iid, ["echo ok"])
    assert "ok" in out
    assert err == ""


def test_send_run_shell_script_get_throttle_then_success(ssm_stubbed_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.node_execution_service.ssm_send_command.time.sleep",
        lambda *_a, **_k: None,
    )
    client, stubber = ssm_stubbed_client
    iid = "i-0d0d0d0d0d0d0d0d"
    cmd_id = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
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
    get_params = {"CommandId": cmd_id, "InstanceId": iid}
    stubber.add_client_error("get_command_invocation", "ThrottlingException", expected_params=get_params)
    stubber.add_response(
        "get_command_invocation",
        {
            "Status": "Success",
            "StandardOutputContent": "y",
            "StandardErrorContent": "",
        },
        get_params,
    )
    out, _ = send_run_shell_script(client, iid, ["true"])
    assert "y" in out


def test_send_run_shell_script_send_throttle_exhausted_raises(ssm_stubbed_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.node_execution_service.ssm_send_command.time.sleep",
        lambda *_a, **_k: None,
    )
    client, stubber = ssm_stubbed_client
    iid = "i-0e0e0e0e0e0e0e0e"
    common_send = {
        "InstanceIds": [iid],
        "DocumentName": "AWS-RunShellScript",
        "Comment": ANY,
        "TimeoutSeconds": ANY,
        "Parameters": {"commands": ["echo x"]},
    }
    for _ in range(5):
        stubber.add_client_error("send_command", "ThrottlingException", expected_params=common_send)
    with pytest.raises(SsmExecutionError, match="ThrottlingException"):
        send_run_shell_script(client, iid, ["echo x"])
