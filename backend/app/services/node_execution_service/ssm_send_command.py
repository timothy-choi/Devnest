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
import re
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
# Upper bound on how long the worker waits for GetCommandInvocation to reach a terminal state.
# Must be compatible with ``TimeoutSeconds`` passed to ``SendCommand`` (long ``docker pull`` / builds).
_MAX_POLL_WAIT_CEILING_S = 7200.0
_MIN_POLL_WAIT_S = 120.0
_SEND_THROTTLE_RETRIES = 4
_GET_THROTTLE_RETRIES = 6

_INSTANCE_ID_RE = re.compile(r"^i-[0-9a-f]{8,32}$", re.IGNORECASE)


def _require_ssm_instance_id(instance_id: str) -> str:
    iid = (instance_id or "").strip()
    if not iid or not _INSTANCE_ID_RE.match(iid):
        raise SsmExecutionError(f"invalid SSM target instance id: {instance_id!r}")
    return iid


def _compute_poll_deadline_seconds(ssm_timeout_seconds: int) -> float:
    """Client-side wait should cover SSM script timeout plus skew, capped for worker safety."""
    return float(min(max(int(ssm_timeout_seconds) + 60, int(_MIN_POLL_WAIT_S)), int(_MAX_POLL_WAIT_CEILING_S)))


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

    **Security:** ``commands`` are executed as root on the instance by SSM. Pass only **trusted**
    content (DevNest-built scripts); never forward unvalidated user input into these strings.

    Returns:
        ``(stdout, stderr)`` from ``GetCommandInvocation`` Standard*Content fields.

    Raises:
        SsmExecutionError: send failed, invocation failed/timed out, or agent unreachable.
    """
    iid = _require_ssm_instance_id(instance_id)
    if not commands:
        raise SsmExecutionError("SSM commands list is empty")

    ssm_timeout = min(max(30, int(timeout_seconds)), 172800)
    poll_wait_s = _compute_poll_deadline_seconds(ssm_timeout)
    if ssm_timeout + 60 > _MAX_POLL_WAIT_CEILING_S:
        logger.info(
            "ssm_poll_wait_capped",
            extra={
                "instance_id": iid,
                "ssm_timeout_seconds": ssm_timeout,
                "poll_wait_seconds": poll_wait_s,
            },
        )

    send_resp = _send_command_with_throttle_retry(
        ssm_client,
        iid,
        comment=comment[:80],
        timeout_seconds=ssm_timeout,
        commands=commands,
    )

    cmd_id = (send_resp.get("Command") or {}).get("CommandId")
    if not cmd_id:
        raise SsmExecutionError("ssm SendCommand returned no CommandId")

    deadline = time.monotonic() + poll_wait_s
    status = "Pending"
    stdout = ""
    stderr = ""
    get_throttle_attempts = 0
    while time.monotonic() < deadline:
        try:
            inv = ssm_client.get_command_invocation(CommandId=cmd_id, InstanceId=iid)
            get_throttle_attempts = 0
        except ClientError as e:
            code = (e.response.get("Error") or {}).get("Code", "")
            if code == "ThrottlingException" and get_throttle_attempts < _GET_THROTTLE_RETRIES:
                get_throttle_attempts += 1
                delay = min(0.25 * (2 ** (get_throttle_attempts - 1)), 8.0)
                logger.warning(
                    "ssm_get_command_invocation_throttled",
                    extra={"instance_id": iid, "attempt": get_throttle_attempts, "sleep_s": delay},
                )
                time.sleep(delay)
                continue
            if code == "InvocationDoesNotExist":
                time.sleep(_POLL_INTERVAL_S)
                continue
            if code in _SSM_AUTH_CODES:
                raise SsmExecutionError(
                    f"AWS denied ssm GetCommandInvocation ({code}); check worker IAM: {e}",
                ) from e
            raise SsmExecutionError(f"ssm GetCommandInvocation failed ({code}): {e}") from e

        status = (inv.get("Status") or "").strip()
        if status in ("Success", "Cancelled", "Failed", "TimedOut", "Undeliverable", "Terminated"):
            stdout = inv.get("StandardOutputContent") or ""
            stderr = inv.get("StandardErrorContent") or ""
            break
        time.sleep(_POLL_INTERVAL_S)

    if status not in ("Success", "Cancelled", "Failed", "TimedOut", "Undeliverable", "Terminated"):
        raise SsmExecutionError(
            f"ssm command {cmd_id!r} on {iid!r} did not finish within {poll_wait_s:.0f}s "
            f"(last status={status!r}); raise timeout_seconds or devnest SSM poll ceiling if legitimate",
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


def _send_command_with_throttle_retry(
    ssm_client: BaseClient,
    instance_id: str,
    *,
    comment: str,
    timeout_seconds: int,
    commands: list[str],
) -> dict[str, Any]:
    for attempt in range(_SEND_THROTTLE_RETRIES + 1):
        try:
            return ssm_client.send_command(
                InstanceIds=[instance_id],
                DocumentName=_AWS_RUN_SHELL,
                Comment=comment,
                TimeoutSeconds=timeout_seconds,
                Parameters={"commands": commands},
            )
        except ClientError as e:
            code = (e.response.get("Error") or {}).get("Code", "")
            if code == "ThrottlingException" and attempt < _SEND_THROTTLE_RETRIES:
                delay = min(0.25 * (2**attempt), 8.0)
                logger.warning(
                    "ssm_send_command_throttled",
                    extra={"instance_id": instance_id, "attempt": attempt + 1, "sleep_s": delay},
                )
                time.sleep(delay)
                continue
            logger.error(
                "ssm_send_command_failed",
                extra={"instance_id": instance_id, "code": code, "error": str(e)},
            )
            if code in _SSM_AUTH_CODES:
                raise SsmExecutionError(
                    f"AWS denied ssm SendCommand ({code}); check credentials, region, and IAM for the worker: {e}",
                ) from e
            raise SsmExecutionError(f"ssm SendCommand failed ({code}): {e}") from e
