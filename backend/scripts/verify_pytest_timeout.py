#!/usr/bin/env python3
"""Verify pytest-timeout is installed and registered with pytest (CI guard).

Run from backend/: ``python scripts/verify_pytest_timeout.py``

Exit non-zero if:
- pytest-timeout cannot be imported, or
- ``python -m pytest --help`` does not advertise timeout options (plugin not loaded).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    try:
        import pytest_timeout  # noqa: F401
    except ImportError:
        print(
            "ERROR: pytest-timeout is not installed. "
            "Add it to requirements.txt and pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    backend_root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--help"],
        capture_output=True,
        text=True,
        cwd=backend_root,
        check=False,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    if "--timeout" not in combined:
        print(
            "ERROR: pytest --help does not list --timeout; pytest-timeout is not active.\n"
            "Install pytest-timeout and ensure pytest can load plugins from backend/pytest.ini.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
