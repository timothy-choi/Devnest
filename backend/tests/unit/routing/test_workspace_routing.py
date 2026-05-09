"""Unit tests: tenant workspace URL helpers (slugify, URL build, host/path parse, slug allocation)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.libs.routing.workspace_routing import (
    allocate_unique_workspace_url_slug,
    build_workspace_url,
    effective_public_base_domain,
    effective_public_scheme,
    extract_workspace_slug_from_path,
    parse_workspace_host,
    slugify_workspace_name,
)
from app.services.auth_service.models import UserAuth
from app.services.workspace_service.models import Workspace
from app.services.workspace_service.models.enums import WorkspaceStatus


def test_slugify_workspace_name_basic():
    assert slugify_workspace_name("ML Lab") == "ml-lab"
    assert slugify_workspace_name("EventRelay") == "eventrelay"
    assert slugify_workspace_name("  Foo   Bar  ") == "foo-bar"


def test_effective_public_base_domain_prefers_explicit():
    s = MagicMock()
    s.devnest_public_base_domain = "devnest.example.com"
    s.devnest_base_domain = "ignored.example.com"
    assert effective_public_base_domain(s) == "devnest.example.com"


def test_effective_public_scheme_prefers_explicit():
    s = MagicMock()
    s.devnest_public_scheme = "https"
    s.devnest_gateway_public_scheme = "http"
    assert effective_public_scheme(s) == "https"


def test_effective_public_scheme_legacy_uses_gateway_when_public_empty():
    s = MagicMock()
    s.devnest_public_scheme = ""
    s.devnest_workspace_domain_mode = ""
    s.devnest_tenant_subdomain_routing_enabled = False
    s.devnest_gateway_public_scheme = "http"
    assert effective_public_scheme(s) == "http"


def test_effective_public_scheme_tenant_defaults_https_when_public_empty():
    s = MagicMock()
    s.devnest_public_scheme = ""
    s.devnest_workspace_domain_mode = "tenant"
    s.devnest_tenant_subdomain_routing_enabled = False
    s.devnest_gateway_public_scheme = "http"
    assert effective_public_scheme(s) == "https"


def test_build_workspace_url_legacy_http_matches_gateway_when_public_scheme_unset():
    user = MagicMock()
    user.route_subdomain_slug = "tim"
    ws = MagicMock()
    ws.workspace_id = 7
    ws.owner_user_id = 1
    ws.url_slug = "x"
    ws.public_host = None
    settings = MagicMock()
    settings.devnest_workspace_domain_mode = ""
    settings.devnest_public_scheme = ""
    settings.devnest_public_port = 0
    settings.devnest_tenant_subdomain_routing_enabled = False
    settings.devnest_gateway_public_scheme = "http"
    settings.devnest_gateway_public_port = 9081
    settings.devnest_public_base_domain = "unused.example.com"
    settings.devnest_base_domain = "1-2-3-4.sslip.io"

    url = build_workspace_url(user=user, workspace=ws, settings=settings)
    assert url == "http://ws-7.1-2-3-4.sslip.io:9081/"


def test_build_workspace_url_tenant_mode():
    user = MagicMock()
    user.route_subdomain_slug = "tim"
    ws = MagicMock()
    ws.workspace_id = 9
    ws.owner_user_id = 3
    ws.url_slug = "eventrelay"
    ws.public_host = None
    settings = MagicMock()
    settings.devnest_workspace_domain_mode = ""
    settings.devnest_public_port = 0
    settings.devnest_tenant_subdomain_routing_enabled = True
    settings.devnest_gateway_public_port = 0
    settings.devnest_public_base_domain = "devnest.example.com"
    settings.devnest_public_scheme = "https"
    settings.devnest_base_domain = "app.devnest.local"

    url = build_workspace_url(user=user, workspace=ws, settings=settings)
    assert url == "https://tim.devnest.example.com/workspaces/eventrelay"


def test_build_workspace_url_legacy_mode():
    user = MagicMock()
    user.route_subdomain_slug = "tim"
    ws = MagicMock()
    ws.workspace_id = 42
    ws.owner_user_id = 3
    ws.url_slug = "x"
    ws.public_host = None
    settings = MagicMock()
    settings.devnest_workspace_domain_mode = ""
    settings.devnest_public_port = 0
    settings.devnest_tenant_subdomain_routing_enabled = False
    settings.devnest_gateway_public_port = 0
    settings.devnest_public_base_domain = "devnest.example.com"
    settings.devnest_public_scheme = "https"
    settings.devnest_base_domain = "app.devnest.local"

    url = build_workspace_url(user=user, workspace=ws, settings=settings)
    assert url == "https://ws-42.app.devnest.local/"


def test_build_workspace_url_domain_mode_tenant_without_legacy_bool():
    user = MagicMock()
    user.route_subdomain_slug = "tim"
    ws = MagicMock()
    ws.workspace_id = 9
    ws.owner_user_id = 3
    ws.url_slug = "eventrelay"
    ws.public_host = None
    settings = MagicMock()
    settings.devnest_workspace_domain_mode = "tenant"
    settings.devnest_public_port = 0
    settings.devnest_tenant_subdomain_routing_enabled = False
    settings.devnest_gateway_public_port = 9081
    settings.devnest_public_base_domain = "devnest.example.com"
    settings.devnest_public_scheme = "https"
    settings.devnest_base_domain = "app.devnest.local"

    url = build_workspace_url(user=user, workspace=ws, settings=settings)
    assert url == "https://tim.devnest.example.com/workspaces/eventrelay"


def test_build_workspace_url_legacy_forced_by_domain_mode():
    user = MagicMock()
    user.route_subdomain_slug = "tim"
    ws = MagicMock()
    ws.workspace_id = 42
    ws.owner_user_id = 3
    ws.url_slug = "should-not-matter"
    ws.public_host = None
    settings = MagicMock()
    settings.devnest_workspace_domain_mode = "legacy"
    settings.devnest_public_port = 0
    settings.devnest_tenant_subdomain_routing_enabled = True
    settings.devnest_gateway_public_port = 0
    settings.devnest_public_base_domain = "devnest.example.com"
    settings.devnest_public_scheme = "https"
    settings.devnest_base_domain = "app.devnest.local"

    url = build_workspace_url(user=user, workspace=ws, settings=settings)
    assert url == "https://ws-42.app.devnest.local/"


def test_build_workspace_url_legacy_emits_legacy_log(caplog):
    user = MagicMock()
    user.route_subdomain_slug = "tim"
    ws = MagicMock()
    ws.workspace_id = 42
    ws.owner_user_id = 3
    ws.url_slug = "x"
    ws.public_host = None
    settings = MagicMock()
    settings.devnest_workspace_domain_mode = "legacy"
    settings.devnest_public_port = 0
    settings.devnest_tenant_subdomain_routing_enabled = False
    settings.devnest_gateway_public_port = 0
    settings.devnest_public_base_domain = "devnest.example.com"
    settings.devnest_public_scheme = "https"
    settings.devnest_base_domain = "app.devnest.local"

    with caplog.at_level(logging.INFO, logger="app.libs.routing.workspace_routing"):
        build_workspace_url(user=user, workspace=ws, settings=settings)
    assert any(r.message == "routing.legacy_url_generated" for r in caplog.records)


def test_build_workspace_url_tenant_emits_routing_workspace_log(caplog):
    user = MagicMock()
    user.route_subdomain_slug = "tim"
    ws = MagicMock()
    ws.workspace_id = 9
    ws.owner_user_id = 3
    ws.url_slug = "eventrelay"
    ws.public_host = None
    settings = MagicMock()
    settings.devnest_workspace_domain_mode = "tenant"
    settings.devnest_public_port = 0
    settings.devnest_tenant_subdomain_routing_enabled = False
    settings.devnest_gateway_public_port = 9081
    settings.devnest_public_base_domain = "devnest.example.com"
    settings.devnest_public_scheme = "https"
    settings.devnest_base_domain = "app.devnest.local"

    with caplog.at_level(logging.INFO, logger="app.libs.routing.workspace_routing"):
        build_workspace_url(user=user, workspace=ws, settings=settings)
    assert any(r.message == "routing.workspace_url_generated" for r in caplog.records)


def test_parse_workspace_host():
    assert parse_workspace_host("tim.devnest.example.com", "devnest.example.com") == "tim"
    assert parse_workspace_host("tim.devnest.example.com:443", "devnest.example.com") == "tim"
    assert parse_workspace_host("wrong.com", "devnest.example.com") is None


def test_extract_workspace_slug_from_path_and_uri():
    assert extract_workspace_slug_from_path("/workspaces/ml-lab") == "ml-lab"
    assert extract_workspace_slug_from_path("/workspaces/ml-lab/extra") == "ml-lab"
    assert extract_workspace_slug_from_path("https://tim.devnest.example.com/workspaces/foo?x=1") == "foo"


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(e)
    return e


def test_allocate_unique_workspace_url_slug_collision_suffix(engine):
    """Same display name twice for one owner yields distinct slugs (suffix -2, -3, …)."""
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        u = UserAuth(username="owner1", email="o1@x.dev", password_hash="x")
        session.add(u)
        session.flush()
        uid = int(u.user_auth_id or 0)

        s1 = allocate_unique_workspace_url_slug(session, owner_user_id=uid, display_name="Project")
        assert s1 == "project"
        session.add(
            Workspace(
                name="Project",
                url_slug=s1,
                owner_user_id=uid,
                status=WorkspaceStatus.STOPPED.value,
                is_private=True,
                active_sessions_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()

        s2 = allocate_unique_workspace_url_slug(session, owner_user_id=uid, display_name="Project")
        assert s2 == "project-2"

        session.add(
            Workspace(
                name="Project",
                url_slug=s2,
                owner_user_id=uid,
                status=WorkspaceStatus.STOPPED.value,
                is_private=True,
                active_sessions_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


def test_gateway_forwardauth_slug_under_workspace_path():
    """Subpaths under /workspaces/<slug>/… still resolve the same workspace slug (WebSockets, assets)."""
    assert extract_workspace_slug_from_path("/workspaces/my-ws/stable-someid/ws") == "my-ws"
