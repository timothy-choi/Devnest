"""Shared types for system tests (importable; do not import ``conftest`` from tests)."""

from __future__ import annotations

from dataclasses import dataclass

from app.libs.runtime.docker_runtime import DockerRuntimeAdapter


@dataclass
class IsolatedRuntimeContext:
    adapter: DockerRuntimeAdapter
    name: str
    workspace_host_path: str
    image: str
