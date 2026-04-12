"""Request/job correlation ID (async-safe ``contextvars``).

Inbound HTTP: :class:`~app.libs.observability.middleware.CorrelationIdMiddleware` sets
``request.state.correlation_id`` (authoritative for sync FastAPI routes) and the contextvar for
async code. New :class:`~app.services.workspace_service.models.WorkspaceJob` rows persist the id
for the worker. :func:`correlation_scope` binds the id around job execution.

**Existing databases:** add column once::

    ALTER TABLE workspace_job ADD COLUMN correlation_id VARCHAR(64);

TODO: propagate correlation into boto3/SSM via botocore event hooks when needed.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

_CORRELATION_ID: ContextVar[str | None] = ContextVar("devnest_correlation_id", default=None)


def generate_correlation_id() -> str:
    return str(uuid.uuid4())


def get_correlation_id() -> str | None:
    return _CORRELATION_ID.get()


def set_correlation_id(correlation_id: str | None) -> Token[str | None]:
    return _CORRELATION_ID.set(correlation_id)


def reset_correlation_id(token: Token[str | None]) -> None:
    _CORRELATION_ID.reset(token)


def resolve_correlation_id(incoming: str | None) -> str:
    """Normalize inbound header value or allocate a new id."""
    raw = (incoming or "").strip()
    if raw and len(raw) <= 128:
        return raw[:64]
    return generate_correlation_id()


@contextmanager
def correlation_scope(correlation_id: str | None) -> Iterator[str]:
    """
    Bind ``correlation_id`` for the block (worker job execution, tests).

    If ``correlation_id`` is empty, generates a fresh id.
    """
    cid = (correlation_id or "").strip() or generate_correlation_id()
    cid = cid[:64]
    token = set_correlation_id(cid)
    try:
        yield cid
    finally:
        reset_correlation_id(token)
