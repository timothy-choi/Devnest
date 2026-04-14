"""Unit tests: reconcile advisory lock helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from sqlmodel import Session

from app.services.reconcile_service.reconcile_lock import (
    release_workspace_reconcile_lock,
    try_acquire_workspace_reconcile_lock,
)


def test_sqlite_always_acquires_and_release_noops() -> None:
    session = MagicMock(spec=Session)
    bind = MagicMock()
    bind.dialect.name = "sqlite"
    session.get_bind.return_value = bind
    assert try_acquire_workspace_reconcile_lock(session, 42) is True
    release_workspace_reconcile_lock(session, 42)
    session.execute.assert_not_called()
