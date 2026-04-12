"""
Run shell on EC2 instances via AWS SSM ``SendCommand`` (``AWS-RunShellScript``).

**IAM — control plane (worker / API host that runs boto3 ``SendCommand``)**

Example policy (tighten ``Resource`` and add ``Condition`` keys such as ``ec2:ResourceTag`` for
production). Replace ``REGION`` and ``ACCOUNT``::

    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Sid": "DevNestSsmRunCommand",
          "Effect": "Allow",
          "Action": [
            "ssm:SendCommand",
            "ssm:GetCommandInvocation",
            "ssm:ListCommandInvocations"
          ],
          "Resource": [
            "arn:aws:ec2:REGION:ACCOUNT:instance/*",
            "arn:aws:ssm:REGION::document/AWS-RunShellScript",
            "arn:aws:ssm:REGION:ACCOUNT:*"
          ]
        }
      ]
    }

- ``SendCommand`` targets are matched against the instance ARN; the document ARN allows the
  ``AWS-RunShellScript`` document (some orgs use ``arn:aws:ssm:*:*:document/*`` during bring-up).
- ``GetCommandInvocation`` / ``ListCommandInvocations`` are evaluated against command resources
  (broader ``arn:aws:ssm:REGION:ACCOUNT:*`` is common in V1).

Optional: restrict instances with ``Condition`` on tags (e.g. ``aws:ResourceTag/devnest:managed``).

**IAM — data plane (EC2 instance)**

Attach AWS managed policy **AmazonSSMManagedInstanceCore** so the SSM Agent registers and can
receive Run Command. The instance role does **not** need ``ssm:SendCommand`` or
``ssm:GetCommandInvocation`` (those are for the **worker** principal calling the API).

**Networking**

Outbound HTTPS to SSM and EC2 messages endpoints (VPC interface endpoints or internet), SSM Agent
installed and running, instance appears as **Managed** in Systems Manager Fleet Manager.

**TODO:** Session Manager preferences, CloudWatch logging for Run Command, rate limiting, document allow-list.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError

from app.libs.common.config import get_settings

from .errors import SsmExecutionError

logger = logging.getLogger(__name__)

_SSM_AUTH_CODES = frozenset(
    {
        "AccessDeniedException",
        "UnauthorizedOperation",
        "AuthFailure",
        "InvalidClientTokenId",
        "ExpiredToken",
    },
)

_AWS_RUN_SHELL = "AWS-RunShellScript"
_POLL_INTERVAL_S = 0.5
_MAX_WAIT_S = 120.0


def build_ssm_client(*, region: str | None = None) -> BaseClient:
    """SSM client using settings + default credential chain (same pattern as EC2 provider)."""
    settings = get_settings()
    kwargs: dict[str, Any] = {}
    r = (region or settings.aws_region or "").strip()
    if r:
        kwargs["region_name"] = r
    key = (settings.aws_access_key_id or "").strip()
    secret = (settings.aws_secret_access_key or "").strip()
    if key and secret:
        kwargs["aws_access_key_id"] = key
        kwargs["aws_secret_access_key"] = secret
    return boto3.client("ssm", **kwargs)


def send_run_shell_script(
    ssm_client: BaseClient,
    instance_id: str,
    commands: list[str],
    *,
    comment: str = "DevNest",
    timeout_seconds: int = 3600,
) -> tuple[str, str]:
    """
    Run ``commands`` (each element is a line of shell) on ``instance_id``.

    Returns:
        ``(stdout, stderr)`` from ``GetCommandInvocation`` Standard*Content fields.

    Raises:
        SsmExecutionError: send failed, invocation failed/timed out, or agent unreachable.
    """
    iid = (instance_id or "").strip()
    if not iid:
        raise SsmExecutionError("SSM instance_id is empty")

    try:
        send_resp = ssm_client.send_command(
            InstanceIds=[iid],
            DocumentName=_AWS_RUN_SHELL,
            Comment=comment[:80],
            TimeoutSeconds=min(max(30, timeout_seconds), 172800),
            Parameters={"commands": commands},
        )
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code", "")
        logger.error("ssm_send_command_failed", extra={"instance_id": iid, "code": code, "error": str(e)})
        if code in _SSM_AUTH_CODES:
            raise SsmExecutionError(
                f"AWS denied ssm SendCommand ({code}); check credentials, region, and IAM for the worker: {e}",
            ) from e
        raise SsmExecutionError(f"ssm SendCommand failed ({code}): {e}") from e

    cmd_id = (send_resp.get("Command") or {}).get("CommandId")
    if not cmd_id:
        raise SsmExecutionError("ssm SendCommand returned no CommandId")

    deadline = time.monotonic() + _MAX_WAIT_S
    status = "Pending"
    stdout = ""
    stderr = ""
    while time.monotonic() < deadline:
        try:
            inv = ssm_client.get_command_invocation(CommandId=cmd_id, InstanceId=iid)
        except ClientError as e:
            code = (e.response.get("Error") or {}).get("Code", "")
            if code == "InvocationDoesNotExist":
                time.sleep(_POLL_INTERVAL_S)
                continue
            raise SsmExecutionError(f"ssm GetCommandInvocation failed ({code}): {e}") from e

        status = (inv.get("Status") or "").strip()
        if status in ("Success", "Cancelled", "Failed", "TimedOut", "Undeliverable", "Terminated"):
            stdout = inv.get("StandardOutputContent") or ""
            stderr = inv.get("StandardErrorContent") or ""
            break
        time.sleep(_POLL_INTERVAL_S)

    if status not in ("Success", "Cancelled", "Failed", "TimedOut", "Undeliverable", "Terminated"):
        raise SsmExecutionError(
            f"ssm command {cmd_id!r} on {iid!r} did not finish within {_MAX_WAIT_S}s (last status={status!r})",
        )

    if status != "Success":
        msg = (stderr or stdout or status or "unknown error").strip()
        logger.warning(
            "ssm_command_non_success",
            extra={"instance_id": iid, "command_id": cmd_id, "status": status, "stderr": stderr[:2000]},
        )
        raise SsmExecutionError(
            f"ssm RunShellScript {status} on {iid!r}: {msg}",
        )

    return str(stdout), str(stderr)
