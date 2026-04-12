"""
Bounded retries for transient AWS API throttling and EC2 rate limits.

Retries ``ThrottlingException`` (many services) and ``RequestLimitExceeded`` (common on EC2
write APIs) with exponential backoff. Does not retry on ``UnauthorizedOperation`` etc.
TODO: optional jitter, per-operation retry budgets for large fleet sync ticks.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

T = TypeVar("T")


def client_call_with_throttle_retry(
    operation: str,
    call: Callable[[], T],
    *,
    max_throttle_retries: int = 5,
    base_delay_s: float = 0.25,
    max_delay_s: float = 8.0,
) -> T:
    """
    Invoke ``call``; on throttling / EC2 rate limit errors sleep with exponential backoff and retry.

    Raises the last ``ClientError`` when retries are exhausted or the error is not retryable.
    """
    retryable = frozenset({"ThrottlingException", "RequestLimitExceeded"})
    for attempt in range(max_throttle_retries + 1):
        try:
            return call()
        except ClientError as e:
            code = (e.response.get("Error") or {}).get("Code", "")
            if code in retryable and attempt < max_throttle_retries:
                delay = min(base_delay_s * (2**attempt), max_delay_s)
                logger.warning(
                    "aws_call_throttled_retry",
                    extra={"operation": operation, "code": code, "attempt": attempt + 1, "sleep_s": delay},
                )
                time.sleep(delay)
                continue
            raise
