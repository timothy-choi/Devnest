#!/usr/bin/env python3
"""Wait until ``DATABASE_URL`` accepts connections (SQLAlchemy + driver already installed).

Avoids ``apt-get install postgresql-client`` on GitHub Actions runners where Ubuntu mirrors stall.
"""

from __future__ import annotations

import os
import sys
import time

from sqlalchemy import create_engine, text


def main() -> None:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        print("ERROR: DATABASE_URL is empty", file=sys.stderr)
        sys.exit(2)
    deadline_s = float((os.environ.get("DEVNEST_DB_WAIT_TIMEOUT_SECONDS") or "120").strip() or "120")
    poll_s = float((os.environ.get("DEVNEST_DB_WAIT_POLL_SECONDS") or "1").strip() or "1")
    started = time.monotonic()
    last_err: Exception | None = None
    attempt = 0
    while time.monotonic() - started < deadline_s:
        attempt += 1
        t0 = time.monotonic()
        try:
            eng = create_engine(url, pool_pre_ping=True)
            with eng.connect() as conn:
                conn.execute(text("SELECT 1"))
            elapsed = time.monotonic() - started
            print(
                f"database_ready elapsed_seconds={elapsed:.2f} attempts={attempt} "
                f"last_probe_seconds={time.monotonic() - t0:.3f}",
                flush=True,
            )
            return
        except Exception as exc:
            last_err = exc
            print(
                f"database_wait attempt={attempt} elapsed_seconds={time.monotonic() - started:.1f} error={exc!r}",
                flush=True,
            )
            time.sleep(max(0.2, poll_s))
    print(
        f"ERROR: database not ready after {deadline_s}s last_error={last_err!r}",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
