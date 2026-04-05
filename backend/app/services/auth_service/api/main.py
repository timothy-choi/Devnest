"""Compatibility shim for older ``uvicorn app.services.auth_service.api.main:app`` invocations."""

from app.main import app

__all__ = ["app"]
