"""Unit tests for route_admin_app (FastAPI → Traefik YAML fragment)."""

from __future__ import annotations

import yaml
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def route_admin_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import route_admin_app as ra

    monkeypatch.setattr(ra, "ROUTES_FILE", tmp_path / "100-workspaces.yml")
    with ra._lock:
        ra._routes.clear()
    yield ra
    with ra._lock:
        ra._routes.clear()


def test_register_and_list(route_admin_module) -> None:
    ra = route_admin_module
    c = TestClient(ra.app)
    r = c.post(
        "/routes",
        json={
            "workspace_id": "7",
            "public_host": "7.app.devnest.local",
            "target": "http://10.0.0.7:8080",
        },
    )
    assert r.status_code == 200
    listed = c.get("/routes").json()
    assert len(listed) == 1
    assert listed[0]["workspace_id"] == "7"
    assert listed[0]["public_host"] == "7.app.devnest.local"
    assert listed[0]["target"] == "http://10.0.0.7:8080"


def test_register_idempotent(route_admin_module) -> None:
    ra = route_admin_module
    c = TestClient(ra.app)
    body = {
        "workspace_id": "1",
        "public_host": "1.app.devnest.local",
        "target": "http://10.0.0.1:8080",
    }
    assert c.post("/routes", json=body).status_code == 200
    assert c.post("/routes", json=body).status_code == 200


def test_register_normalizes_target(route_admin_module) -> None:
    ra = route_admin_module
    c = TestClient(ra.app)
    r = c.post(
        "/routes",
        json={
            "workspace_id": "2",
            "public_host": "2.app.devnest.local",
            "target": "192.168.1.2:9090",
        },
    )
    assert r.status_code == 200
    assert r.json()["target"] == "http://192.168.1.2:9090"


def test_deregister_idempotent(route_admin_module) -> None:
    ra = route_admin_module
    c = TestClient(ra.app)
    c.post(
        "/routes",
        json={
            "workspace_id": "3",
            "public_host": "3.app.devnest.local",
            "target": "http://10.0.0.3:8080",
        },
    )
    assert c.delete("/routes/3").status_code == 204
    assert c.delete("/routes/3").status_code == 204


def test_persist_writes_traefik_shape(route_admin_module) -> None:
    ra = route_admin_module
    c = TestClient(ra.app)
    c.post(
        "/routes",
        json={
            "workspace_id": "99",
            "public_host": "99.app.devnest.local",
            "target": "http://10.9.9.9:8080",
        },
    )
    text = ra.ROUTES_FILE.read_text(encoding="utf-8")
    cfg = yaml.safe_load(text)
    rname = "devnest-reg-99"
    assert cfg["http"]["routers"][rname]["rule"] == "Host(`99.app.devnest.local`)"
    assert cfg["http"]["services"][f"{rname}-upstream"]["loadBalancer"]["servers"][0]["url"] == (
        "http://10.9.9.9:8080"
    )
