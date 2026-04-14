"""Unit tests for workspace optional feature flags (Task 14: feature config-gating)."""

from __future__ import annotations

import pytest

from app.services.workspace_service.api.schemas.workspace_schemas import (
    WorkspaceFeatureFlags,
    WorkspaceRuntimeSpecSchema,
    get_workspace_features,
)


class TestWorkspaceFeatureFlags:
    def test_defaults_all_false(self) -> None:
        flags = WorkspaceFeatureFlags()
        assert flags.terminal_enabled is False
        assert flags.ci_enabled is False
        assert flags.ai_tools_enabled is False

    def test_can_enable_terminal(self) -> None:
        flags = WorkspaceFeatureFlags(terminal_enabled=True)
        assert flags.terminal_enabled is True
        assert flags.ci_enabled is False

    def test_model_dump_round_trips(self) -> None:
        flags = WorkspaceFeatureFlags(terminal_enabled=True, ci_enabled=True)
        d = flags.model_dump()
        restored = WorkspaceFeatureFlags.model_validate(d)
        assert restored.terminal_enabled is True
        assert restored.ci_enabled is True
        assert restored.ai_tools_enabled is False


class TestGetWorkspaceFeatures:
    def test_returns_defaults_for_none_config(self) -> None:
        flags = get_workspace_features(None)
        assert flags.terminal_enabled is False
        assert flags.ci_enabled is False

    def test_returns_defaults_for_empty_config(self) -> None:
        flags = get_workspace_features({})
        assert flags.terminal_enabled is False

    def test_returns_defaults_when_features_missing(self) -> None:
        flags = get_workspace_features({"image": "nginx"})
        assert flags.terminal_enabled is False

    def test_reads_terminal_enabled(self) -> None:
        flags = get_workspace_features({"features": {"terminal_enabled": True}})
        assert flags.terminal_enabled is True
        assert flags.ci_enabled is False

    def test_reads_multiple_flags(self) -> None:
        flags = get_workspace_features(
            {"features": {"terminal_enabled": True, "ci_enabled": True, "ai_tools_enabled": True}}
        )
        assert flags.terminal_enabled is True
        assert flags.ci_enabled is True
        assert flags.ai_tools_enabled is True

    def test_ignores_non_dict_features(self) -> None:
        flags = get_workspace_features({"features": "bad_value"})
        assert flags.terminal_enabled is False

    def test_forward_compatible_with_unknown_keys(self) -> None:
        flags = get_workspace_features({"features": {"terminal_enabled": True, "future_feature": True}})
        assert flags.terminal_enabled is True


class TestWorkspaceRuntimeSpecSchemaFeatures:
    def test_to_config_dict_includes_features(self) -> None:
        spec = WorkspaceRuntimeSpecSchema(
            features=WorkspaceFeatureFlags(terminal_enabled=True),
        )
        d = spec.to_config_dict()
        assert "features" in d
        assert d["features"]["terminal_enabled"] is True
        assert d["features"]["ci_enabled"] is False

    def test_to_config_dict_features_default_all_false(self) -> None:
        spec = WorkspaceRuntimeSpecSchema()
        d = spec.to_config_dict()
        assert d["features"]["terminal_enabled"] is False

    def test_round_trip_features_in_config_dict(self) -> None:
        spec = WorkspaceRuntimeSpecSchema(
            features=WorkspaceFeatureFlags(terminal_enabled=True, ai_tools_enabled=True)
        )
        config_dict = spec.to_config_dict()
        restored = get_workspace_features(config_dict)
        assert restored.terminal_enabled is True
        assert restored.ai_tools_enabled is True
        assert restored.ci_enabled is False
