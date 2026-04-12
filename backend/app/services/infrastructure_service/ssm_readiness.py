"""Minimal SSM fleet check for EC2 readiness (no Run Command)."""

from __future__ import annotations

import logging
from typing import Any

from botocore.client import BaseClient
from botocore.exceptions import ClientError

from app.services.providers.aws_throttle import client_call_with_throttle_retry

logger = logging.getLogger(__name__)


def is_instance_ssm_online(ssm_client: BaseClient, instance_id: str) -> bool:
    """
    Return True if the instance appears in SSM as managed with ``PingStatus == Online``.

    Requires worker IAM ``ssm:DescribeInstanceInformation``. The instance role still needs
    **AmazonSSMManagedInstanceCore** (or equivalent) for the agent to register.

    Retries ``ThrottlingException`` with bounded backoff. TODO: shared rate budget when polling
    many instances from a single sync tick.
    """
    iid = (instance_id or "").strip()
    if not iid:
        return False
    try:
        resp = client_call_with_throttle_retry(
            "ssm.DescribeInstanceInformation",
            lambda: ssm_client.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [iid]}],
                MaxResults=5,
            ),
            max_throttle_retries=4,
        )
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code", "")
        logger.warning(
            "ssm_describe_instance_information_failed",
            extra={"instance_id": iid, "code": code},
        )
        return False
    infos: list[dict[str, Any]] = list(resp.get("InstanceInformationList") or [])
    if not infos:
        return False
    ping = (infos[0].get("PingStatus") or "").strip()
    return ping == "Online"
