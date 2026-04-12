"""Unit tests: EC2/SSM throttle retry helper."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from app.services.providers.aws_throttle import client_call_with_throttle_retry


def test_throttle_retry_then_success(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(
        "app.services.providers.aws_throttle.time.sleep",
        lambda s: sleeps.append(float(s)),
    )
    mock = MagicMock(
        side_effect=[
            ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "RunInstances"),
            ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "RunInstances"),
            {"ok": True},
        ],
    )
    out = client_call_with_throttle_retry("test.op", mock, max_throttle_retries=5)
    assert out == {"ok": True}
    assert mock.call_count == 3
    assert len(sleeps) == 2


def test_request_limit_exceeded_retry_then_success(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(
        "app.services.providers.aws_throttle.time.sleep",
        lambda s: sleeps.append(float(s)),
    )
    mock = MagicMock(
        side_effect=[
            ClientError({"Error": {"Code": "RequestLimitExceeded", "Message": "rate"}}, "RunInstances"),
            {"Instances": [{"InstanceId": "i-0a1b2c3d4e5f6789"}]},
        ],
    )
    out = client_call_with_throttle_retry("ec2.RunInstances", mock, max_throttle_retries=3)
    assert out["Instances"][0]["InstanceId"] == "i-0a1b2c3d4e5f6789"
    assert mock.call_count == 2
    assert len(sleeps) == 1


def test_throttle_exhausted_raises() -> None:
    err = ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "X")
    mock = MagicMock(side_effect=err)
    with pytest.raises(ClientError):
        client_call_with_throttle_retry("test.op", mock, max_throttle_retries=2)
    assert mock.call_count == 3
