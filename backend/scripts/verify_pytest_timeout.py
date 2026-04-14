#!/usr/bin/env python3
"""Exit0 if pytest-timeout is importable (CI guard). Run from backend/: python scripts/verify_pytest_timeout.py"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        import pytest_timeout  # noqa: F401
    except ImportError:
        print(
            "ERROR: pytest-timeout is not installed. Add it to requirements and pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
