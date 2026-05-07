"""GET /auth/public/route-tenants/{subdomain} — existence probe for frontend middleware."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.libs.db.database import get_db
from app.services.auth_service.api.routers.auth import router
from app.services.auth_service.models import UserAuth


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(e)
    return e


def _make_app(engine):
    app = FastAPI()
    app.include_router(router)

    def _db():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db] = _db
    return app


def test_route_tenant_unknown_returns_404(engine):
    client = TestClient(_make_app(engine))
    r = client.get("/auth/public/route-tenants/nope-" + uuid.uuid4().hex[:6])
    assert r.status_code == 404


def test_route_tenant_known_returns_204(engine):
    slug = "alice-" + uuid.uuid4().hex[:6]
    with Session(engine) as s:
        s.add(
            UserAuth(
                username=f"u-{slug}",
                email=f"u-{slug}@t.devnest.local",
                password_hash="x",
                route_subdomain_slug=slug,
            )
        )
        s.commit()
    client = TestClient(_make_app(engine))
    r = client.get(f"/auth/public/route-tenants/{slug}")
    assert r.status_code == 204
