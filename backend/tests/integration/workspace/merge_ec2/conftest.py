"""Merge-tier EC2-profile lifecycle: skip (not fail) when Docker is unavailable (e.g. some CI runners).

Overrides parent ``docker_client`` so optional pipelines without a daemon still pass the rest of
integration; on machines with Docker this runs the full lifecycle proof.
"""

from __future__ import annotations

from collections.abc import Generator

import docker
import pytest


@pytest.fixture(scope="session")
def docker_client() -> Generator[docker.DockerClient, None, None]:
    try:
        client = docker.from_env()
        client.ping()
    except Exception as e:
        pytest.skip(f"Docker daemon required for merge EC2 lifecycle test: {e}")
    yield client
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass
