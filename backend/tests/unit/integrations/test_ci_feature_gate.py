"""Unit tests: CI endpoint feature-gate enforcement (ci_enabled flag)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.integration_service.api.routers.workspace_ci import _assert_ci_enabled
from app.services.workspace_service.models import WorkspaceConfig


def _make_session(config_json: dict | None) -> MagicMock:
    """Return a mock Session whose exec().first() returns a WorkspaceConfig with config_json."""
    cfg = None if config_json is None else WorkspaceConfig(
        workspace_id=1, version=1, config_json=config_json
    )
    mock_exec = MagicMock()
    mock_exec.first.return_value = cfg
    session = MagicMock()
    session.exec.return_value = mock_exec
    return session


class TestAssertCiEnabled:
    def test_raises_403_when_ci_disabled_by_default(self):
        session = _make_session({"features": {}})
        with pytest.raises(HTTPException) as exc_info:
            _assert_ci_enabled(session, workspace_id=1)
        assert exc_info.value.status_code == 403

    def test_raises_403_when_ci_explicitly_false(self):
        session = _make_session({"features": {"ci_enabled": False}})
        with pytest.raises(HTTPException) as exc_info:
            _assert_ci_enabled(session, workspace_id=1)
        assert exc_info.value.status_code == 403

    def test_raises_403_when_no_config_exists(self):
        """Workspace has no WorkspaceConfig row → defaults all features to False."""
        session = _make_session(None)
        with pytest.raises(HTTPException) as exc_info:
            _assert_ci_enabled(session, workspace_id=1)
        assert exc_info.value.status_code == 403

    def test_does_not_raise_when_ci_enabled_true(self):
        session = _make_session({"features": {"ci_enabled": True}})
        _assert_ci_enabled(session, workspace_id=1)  # must not raise

    def test_error_message_mentions_feature(self):
        session = _make_session({"features": {}})
        with pytest.raises(HTTPException) as exc_info:
            _assert_ci_enabled(session, workspace_id=1)
        assert "ci_enabled" in exc_info.value.detail.lower() or "ci feature" in exc_info.value.detail.lower()

    def test_ci_enabled_true_with_other_features(self):
        """ci_enabled=True alongside other flags still passes."""
        session = _make_session({
            "features": {
                "ci_enabled": True,
                "terminal_enabled": False,
                "ai_tools_enabled": False,
            }
        })
        _assert_ci_enabled(session, workspace_id=1)  # must not raise
