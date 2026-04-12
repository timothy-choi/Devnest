"""Unit tests: scoped internal API credentials."""

from __future__ import annotations

import pytest

from app.libs.common.config import Settings, get_settings
from app.libs.security.internal_auth import (
    InternalApiScope,
    internal_api_expected_secrets,
    internal_api_key_is_valid,
)


def _s(**kwargs: object) -> Settings:
    return Settings(database_url="postgresql://localhost/unit", **kwargs)


def test_legacy_key_used_when_scope_key_unset() -> None:
    s = _s(internal_api_key="shared-secret")
    assert internal_api_expected_secrets(s, InternalApiScope.WORKSPACE_JOBS) == ("shared-secret",)
    assert internal_api_key_is_valid("shared-secret", s, InternalApiScope.NOTIFICATIONS) is True


def test_scoped_key_replaces_legacy_for_that_surface() -> None:
    s = _s(internal_api_key="legacy", internal_api_key_notifications="notif-only")
    assert internal_api_expected_secrets(s, InternalApiScope.NOTIFICATIONS) == ("notif-only",)
    assert internal_api_key_is_valid("legacy", s, InternalApiScope.NOTIFICATIONS) is False
    assert internal_api_key_is_valid("notif-only", s, InternalApiScope.NOTIFICATIONS) is True


def test_whitespace_stripped_on_scoped_and_legacy() -> None:
    s = _s(internal_api_key="  legacy  ")
    assert internal_api_key_is_valid("legacy", s, InternalApiScope.AUTOSCALER) is True


def test_empty_header_invalid() -> None:
    s = _s(internal_api_key="k")
    assert internal_api_key_is_valid(None, s, InternalApiScope.INFRASTRUCTURE) is False
    assert internal_api_key_is_valid("", s, InternalApiScope.INFRASTRUCTURE) is False
    assert internal_api_key_is_valid("   ", s, InternalApiScope.INFRASTRUCTURE) is False


def test_unconfigured_scope_has_no_secrets() -> None:
    s = _s(internal_api_key="")
    assert internal_api_expected_secrets(s, InternalApiScope.WORKSPACE_JOBS) == ()
    assert internal_api_key_is_valid("x", s, InternalApiScope.WORKSPACE_JOBS) is False


def test_dependency_factory_registers_distinct_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke: FastAPI can bind two routers with different scope closures."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/unit")
    monkeypatch.setenv("INTERNAL_API_KEY", "")
    monkeypatch.setenv("INTERNAL_API_KEY_NOTIFICATIONS", "n")
    monkeypatch.setenv("INTERNAL_API_KEY_WORKSPACE_JOBS", "j")
    get_settings.cache_clear()
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from app.libs.security.dependencies import require_internal_api_key
    from app.libs.security.internal_auth import InternalApiScope

    app = FastAPI()

    @app.get("/n", dependencies=[Depends(require_internal_api_key(InternalApiScope.NOTIFICATIONS))])
    def n() -> dict[str, str]:
        return {"ok": "n"}

    @app.get("/j", dependencies=[Depends(require_internal_api_key(InternalApiScope.WORKSPACE_JOBS))])
    def j() -> dict[str, str]:
        return {"ok": "j"}

    with TestClient(app) as c:
        assert c.get("/n", headers={"X-Internal-API-Key": "n"}).status_code == 200
        assert c.get("/n", headers={"X-Internal-API-Key": "j"}).status_code == 401
        assert c.get("/j", headers={"X-Internal-API-Key": "j"}).status_code == 200
        assert c.get("/j", headers={"X-Internal-API-Key": "n"}).status_code == 401
    get_settings.cache_clear()
