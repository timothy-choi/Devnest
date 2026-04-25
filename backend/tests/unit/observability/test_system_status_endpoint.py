"""Tests for GET /system/status (authenticated deployment summary)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.libs.db.database import get_db
from app.libs.observability.system_status_routes import router
from app.services.auth_service.api.dependencies import get_current_user
from app.services.auth_service.models.user_auth import UserAuth


def _fake_user() -> UserAuth:
    u = UserAuth(username="status_tester", password_hash="x", email="status@test.local")
    u.user_auth_id = 42
    return u


@pytest.fixture
def status_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = _fake_user

    session = MagicMock()
    counts = iter([7, 2])

    def _exec(_stmt):  # noqa: ANN001
        m = MagicMock()
        m.one.return_value = next(counts)
        return m

    session.exec.side_effect = _exec

    def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def test_system_status_401_without_bearer() -> None:
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        r = c.get("/system/status")
    assert r.status_code == 401


@patch("app.libs.observability.system_status_routes.in_process_workspace_worker_running", return_value=False)
@patch("app.libs.observability.system_status_routes.snapshot_storage_log_fields")
@patch("app.libs.observability.system_status_routes.get_settings")
@patch("app.libs.observability.system_status_routes.get_engine")
def test_system_status_ok_json_shape(
    mock_engine: MagicMock,
    mock_settings: MagicMock,
    mock_snap_fields: MagicMock,
    _mock_inproc: MagicMock,
    status_client: TestClient,
) -> None:
    mock_settings.return_value.database_url = "postgresql+psycopg://u:p@db.example:5432/app"
    mock_settings.return_value.devnest_worker_enabled = False
    mock_settings.return_value.devnest_gateway_enabled = True
    mock_settings.return_value.devnest_base_domain = "app.example.test"
    mock_settings.return_value.devnest_gateway_public_scheme = "https"
    mock_settings.return_value.devnest_gateway_public_port = 443
    mock_settings.return_value.devnest_gateway_auth_enabled = True
    mock_settings.return_value.devnest_gateway_url = "http://route-admin:8080"
    mock_settings.return_value.devnest_env = "development"
    mock_snap_fields.return_value = {
        "provider": "local",
        "bucket": "-",
        "prefix": "-",
        "region": "-",
        "root": "/tmp/snapshots",
    }
    conn = MagicMock()
    mock_engine.return_value.connect.return_value.__enter__ = lambda s: conn
    mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)

    r = status_client.get("/system/status", headers={"Authorization": "Bearer dummy"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["database_connected"] is True
    assert data["database_host"] == "db.example"
    assert data["database_name"] == "app"
    assert data["snapshot_storage"]["provider"] == "local"
    assert data["gateway"]["enabled"] is True
    assert data["gateway"]["base_domain"] == "app.example.test"
    assert data["worker"]["deployment_model"] == "standalone"
    assert data["worker"]["jobs_queued"] == 7
    assert data["worker"]["jobs_running"] == 2
    assert "generated_at" in data
