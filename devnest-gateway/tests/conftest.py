from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def gateway_root() -> Path:
    """Directory containing docker-compose.yml (devnest-gateway/)."""
    return Path(__file__).resolve().parents[1]
