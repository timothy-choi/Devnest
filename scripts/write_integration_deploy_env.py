#!/usr/bin/env python3
"""
Write or validate repo-root .env.integration for EC2 / GitHub Actions deploy.

Write mode reads DEVNEST_CI_WRITE_* from the process environment (CI SSH interpolates secrets
into the remote shell before invoking this script). Values are written with safe quoting so
URLs with & ? = are not mangled by the shell.

Requires SQLAlchemy-style Postgres DSN:
  postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require
Rejects libpq keyword-style DSNs (host=... port=... dbname=...).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

# Keys merged from os.environ in sync-from-env (after deploy-ec2.sh normalizes the shell).
SYNC_FROM_ENV_KEYS = (
    "DEVNEST_FRONTEND_PUBLIC_BASE_URL",
    "NEXT_PUBLIC_APP_BASE_URL",
    "GITHUB_OAUTH_PUBLIC_BASE_URL",
    "GCLOUD_OAUTH_PUBLIC_BASE_URL",
    "NEXT_PUBLIC_API_BASE_URL",
    "DEVNEST_BASE_DOMAIN",
    "DEVNEST_GATEWAY_PUBLIC_SCHEME",
    "DEVNEST_GATEWAY_PUBLIC_PORT",
)


def _is_libpq_keyword_dsn(s: str) -> bool:
    t = s.strip()
    if not t:
        return False
    low = t.lower()
    if "://" in t:
        return False
    if low.startswith("host=") or " host=" in low:
        return True
    if "dbname=" in low and "postgresql+psycopg://" not in t:
        return True
    return False


def validate_postgresql_psycopg_url(name: str, url: str) -> None:
    raw = url or ""
    if "\n" in raw or "\r" in raw:
        raise ValueError(f"{name} must be a single line (no embedded newlines); check GitHub secret / CI quoting")
    url = raw.strip()
    if not url:
        raise ValueError(f"{name} is empty")
    if _is_libpq_keyword_dsn(url):
        raise ValueError(
            f"{name} looks like libpq keyword format. Use a single-line SQLAlchemy URL, e.g. "
            "postgresql+psycopg://USER:PASSWORD@HOST:5432/DB?sslmode=require"
        )
    if not url.startswith("postgresql+psycopg://"):
        raise ValueError(
            f"{name} must start with postgresql+psycopg:// (SQLAlchemy driver); got {url[:48]!r}..."
        )
    parsed = urlparse(url)
    if parsed.scheme != "postgresql+psycopg":
        raise ValueError(f"{name}: invalid scheme {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError(f"{name}: missing host")
    dbname = (parsed.path or "").strip("/")
    if not dbname:
        raise ValueError(f"{name}: missing database name in path (expected …/DBNAME?…)")


def _nonempty(name: str, val: str | None) -> str:
    v = (val or "").strip()
    if not v:
        raise ValueError(f"{name} is required but empty")
    return v


def _httpish_base(name: str, val: str) -> None:
    v = val.strip()
    if not v.startswith(("http://", "https://")):
        raise ValueError(f"{name} must start with http:// or https://")


def format_env_line(key: str, val: str) -> str:
    """One KEY=value line; always double-quote values so URLs with & ? = survive Docker env-file parsing."""
    val = val.replace("\r", "").replace("\n", "")
    if "\n" in val:
        raise ValueError(f"{key}: value must not contain newlines")
    esc = val.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}="{esc}"\n'


def _parse_env_value(raw: str) -> str:
    v = raw.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        inner = v[1:-1]
        out: list[str] = []
        i = 0
        while i < len(inner):
            if inner[i] == "\\" and i + 1 < len(inner):
                out.append(inner[i + 1])
                i += 2
            else:
                out.append(inner[i])
                i += 1
        return "".join(out)
    return v


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, rest = line.partition("=")
        k = k.strip()
        out[k] = _parse_env_value(rest)
    return out


def write_dict(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Stable order: DB first, then gateway/public, S3, OAuth
    order = [
        "DATABASE_URL",
        "DEVNEST_COMPOSE_DATABASE_URL",
        "DEVNEST_DATABASE_URL",
        "DEVNEST_BASE_DOMAIN",
        "DEVNEST_GATEWAY_PUBLIC_SCHEME",
        "DEVNEST_GATEWAY_PUBLIC_PORT",
        "DEVNEST_FRONTEND_PUBLIC_BASE_URL",
        "NEXT_PUBLIC_APP_BASE_URL",
        "NEXT_PUBLIC_API_BASE_URL",
        "GITHUB_OAUTH_PUBLIC_BASE_URL",
        "GCLOUD_OAUTH_PUBLIC_BASE_URL",
        "DEVNEST_SNAPSHOT_STORAGE_PROVIDER",
        "DEVNEST_S3_SNAPSHOT_BUCKET",
        "DEVNEST_S3_SNAPSHOT_PREFIX",
        "AWS_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "OAUTH_GITHUB_CLIENT_ID",
        "OAUTH_GITHUB_CLIENT_SECRET",
        "OAUTH_GOOGLE_CLIENT_ID",
        "OAUTH_GOOGLE_CLIENT_SECRET",
    ]
    extras = sorted(k for k in data if k not in order)
    keys = [k for k in order if k in data] + extras
    text = "".join(format_env_line(k, data[k]) for k in keys)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)


def cmd_write(args: argparse.Namespace) -> int:
    try:
        return _cmd_write_body()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _cmd_write_body() -> int:
    db = os.environ.get("DEVNEST_CI_WRITE_DATABASE_URL", "").strip()
    if not db:
        print("ERROR: DEVNEST_CI_WRITE_DATABASE_URL is required.", file=sys.stderr)
        return 1
    validate_postgresql_psycopg_url("DEVNEST_CI_WRITE_DATABASE_URL", db)

    bucket = _nonempty("DEVNEST_CI_WRITE_S3_BUCKET", os.environ.get("DEVNEST_CI_WRITE_S3_BUCKET"))
    region = _nonempty("DEVNEST_CI_WRITE_AWS_REGION", os.environ.get("DEVNEST_CI_WRITE_AWS_REGION"))
    provider = (os.environ.get("DEVNEST_CI_WRITE_SNAPSHOT_PROVIDER") or "s3").strip()
    if provider != "s3":
        print("ERROR: DEVNEST_CI_WRITE_SNAPSHOT_PROVIDER must be s3 for RDS deploy.", file=sys.stderr)
        return 1
    prefix = (os.environ.get("DEVNEST_CI_WRITE_S3_PREFIX") or "devnest-snapshots").strip()

    fe = _nonempty("DEVNEST_CI_WRITE_FRONTEND_PUBLIC_BASE_URL", os.environ.get("DEVNEST_CI_WRITE_FRONTEND_PUBLIC_BASE_URL"))
    gh_pub = (os.environ.get("DEVNEST_CI_WRITE_GITHUB_OAUTH_PUBLIC_BASE_URL") or fe).strip()
    gc_pub = (os.environ.get("DEVNEST_CI_WRITE_GCLOUD_OAUTH_PUBLIC_BASE_URL") or fe).strip()
    for n, v in (
        ("DEVNEST_CI_WRITE_FRONTEND_PUBLIC_BASE_URL", fe),
        ("DEVNEST_CI_WRITE_GITHUB_OAUTH_PUBLIC_BASE_URL", gh_pub),
        ("DEVNEST_CI_WRITE_GCLOUD_OAUTH_PUBLIC_BASE_URL", gc_pub),
    ):
        _httpish_base(n, v)

    base_domain = (os.environ.get("DEVNEST_CI_WRITE_BASE_DOMAIN") or "").strip()
    if not base_domain:
        print("ERROR: DEVNEST_CI_WRITE_BASE_DOMAIN is required (hostname for ws-*.domain, no scheme).", file=sys.stderr)
        return 1
    gw_scheme = (os.environ.get("DEVNEST_CI_WRITE_GATEWAY_PUBLIC_SCHEME") or "http").strip()
    gw_port = (os.environ.get("DEVNEST_CI_WRITE_GATEWAY_PUBLIC_PORT") or "9081").strip()

    next_api = (os.environ.get("DEVNEST_CI_WRITE_NEXT_PUBLIC_API_BASE_URL") or "").strip()
    if not next_api:
        print("ERROR: DEVNEST_CI_WRITE_NEXT_PUBLIC_API_BASE_URL is required.", file=sys.stderr)
        return 1
    _httpish_base("DEVNEST_CI_WRITE_NEXT_PUBLIC_API_BASE_URL", next_api)
    next_app = (os.environ.get("DEVNEST_CI_WRITE_NEXT_PUBLIC_APP_BASE_URL") or fe).strip()
    _httpish_base("DEVNEST_CI_WRITE_NEXT_PUBLIC_APP_BASE_URL", next_app)

    ogh_id = _nonempty("DEVNEST_CI_WRITE_OAUTH_GITHUB_ID", os.environ.get("DEVNEST_CI_WRITE_OAUTH_GITHUB_ID"))
    ogh_sec = _nonempty("DEVNEST_CI_WRITE_OAUTH_GITHUB_SECRET", os.environ.get("DEVNEST_CI_WRITE_OAUTH_GITHUB_SECRET"))
    ogo_id = (os.environ.get("DEVNEST_CI_WRITE_OAUTH_GOOGLE_ID") or "").strip()
    ogo_sec = (os.environ.get("DEVNEST_CI_WRITE_OAUTH_GOOGLE_SECRET") or "").strip()
    if ogo_id or ogo_sec:
        ogo_id = _nonempty("DEVNEST_CI_WRITE_OAUTH_GOOGLE_ID", ogo_id)
        ogo_sec = _nonempty("DEVNEST_CI_WRITE_OAUTH_GOOGLE_SECRET", ogo_sec)

    repo = Path(os.environ.get("DEVNEST_DEPLOY_DIR", os.path.expanduser("~/Devnest"))).resolve()
    out = repo / ".env.integration"

    data: dict[str, str] = {
        "DATABASE_URL": db,
        "DEVNEST_COMPOSE_DATABASE_URL": db,
        "DEVNEST_DATABASE_URL": db,
        "DEVNEST_BASE_DOMAIN": base_domain,
        "DEVNEST_GATEWAY_PUBLIC_SCHEME": gw_scheme,
        "DEVNEST_GATEWAY_PUBLIC_PORT": gw_port,
        "DEVNEST_FRONTEND_PUBLIC_BASE_URL": fe,
        "NEXT_PUBLIC_APP_BASE_URL": next_app,
        "NEXT_PUBLIC_API_BASE_URL": next_api,
        "GITHUB_OAUTH_PUBLIC_BASE_URL": gh_pub,
        "GCLOUD_OAUTH_PUBLIC_BASE_URL": gc_pub,
        "DEVNEST_SNAPSHOT_STORAGE_PROVIDER": provider,
        "DEVNEST_S3_SNAPSHOT_BUCKET": bucket,
        "DEVNEST_S3_SNAPSHOT_PREFIX": prefix,
        "AWS_REGION": region,
        "OAUTH_GITHUB_CLIENT_ID": ogh_id,
        "OAUTH_GITHUB_CLIENT_SECRET": ogh_sec,
    }
    if ogo_id and ogo_sec:
        data["OAUTH_GOOGLE_CLIENT_ID"] = ogo_id
        data["OAUTH_GOOGLE_CLIENT_SECRET"] = ogo_sec
    ak = os.environ.get("DEVNEST_CI_WRITE_AWS_ACCESS_KEY_ID", "").strip()
    sk = os.environ.get("DEVNEST_CI_WRITE_AWS_SECRET_ACCESS_KEY", "").strip()
    if ak:
        data["AWS_ACCESS_KEY_ID"] = ak
    if sk:
        data["AWS_SECRET_ACCESS_KEY"] = sk

    write_dict(out, data)
    print(f"Wrote {out} (mode 0600).")
    g_ok = "ok" if (ogo_id and ogo_sec) else "skipped"
    print(
        "Presence (no secret values): DATABASE_URL=ok DEVNEST_S3_SNAPSHOT_BUCKET=ok AWS_REGION=ok "
        f"OAUTH_GITHUB=ok OAUTH_GOOGLE={g_ok} public_bases=ok gateway=ok"
    )
    return 0


def _is_bundled_compose_postgres(url: str) -> bool:
    return "@postgres:" in url or "@postgres/" in url


def validate_parsed(data: dict[str, str]) -> None:
    db = (data.get("DATABASE_URL") or "").strip()
    if not db:
        raise ValueError("DATABASE_URL missing in file")
    validate_postgresql_psycopg_url("DATABASE_URL", db)
    d2 = (data.get("DEVNEST_COMPOSE_DATABASE_URL") or "").strip()
    d3 = (data.get("DEVNEST_DATABASE_URL") or "").strip()
    if d2 != db or d3 != db:
        raise ValueError("DATABASE_URL, DEVNEST_COMPOSE_DATABASE_URL, and DEVNEST_DATABASE_URL must match")

    if _is_bundled_compose_postgres(db):
        return

    _nonempty("DEVNEST_SNAPSHOT_STORAGE_PROVIDER", data.get("DEVNEST_SNAPSHOT_STORAGE_PROVIDER"))
    if (data.get("DEVNEST_SNAPSHOT_STORAGE_PROVIDER") or "").strip() != "s3":
        raise ValueError("DEVNEST_SNAPSHOT_STORAGE_PROVIDER must be s3 for external Postgres")
    _nonempty("DEVNEST_S3_SNAPSHOT_BUCKET", data.get("DEVNEST_S3_SNAPSHOT_BUCKET"))
    _nonempty("AWS_REGION", data.get("AWS_REGION"))
    _nonempty("DEVNEST_S3_SNAPSHOT_PREFIX", data.get("DEVNEST_S3_SNAPSHOT_PREFIX"))

    _nonempty("DEVNEST_BASE_DOMAIN", data.get("DEVNEST_BASE_DOMAIN"))
    _nonempty("DEVNEST_GATEWAY_PUBLIC_SCHEME", data.get("DEVNEST_GATEWAY_PUBLIC_SCHEME"))
    _nonempty("DEVNEST_GATEWAY_PUBLIC_PORT", data.get("DEVNEST_GATEWAY_PUBLIC_PORT"))

    fe = _nonempty("DEVNEST_FRONTEND_PUBLIC_BASE_URL", data.get("DEVNEST_FRONTEND_PUBLIC_BASE_URL"))
    _httpish_base("DEVNEST_FRONTEND_PUBLIC_BASE_URL", fe)
    _nonempty("NEXT_PUBLIC_APP_BASE_URL", data.get("NEXT_PUBLIC_APP_BASE_URL"))
    _httpish_base("NEXT_PUBLIC_APP_BASE_URL", data["NEXT_PUBLIC_APP_BASE_URL"])
    _nonempty("NEXT_PUBLIC_API_BASE_URL", data.get("NEXT_PUBLIC_API_BASE_URL"))
    _httpish_base("NEXT_PUBLIC_API_BASE_URL", data["NEXT_PUBLIC_API_BASE_URL"])
    for k in ("GITHUB_OAUTH_PUBLIC_BASE_URL", "GCLOUD_OAUTH_PUBLIC_BASE_URL"):
        v = _nonempty(k, data.get(k))
        _httpish_base(k, v)

    _nonempty("OAUTH_GITHUB_CLIENT_ID", data.get("OAUTH_GITHUB_CLIENT_ID"))
    _nonempty("OAUTH_GITHUB_CLIENT_SECRET", data.get("OAUTH_GITHUB_CLIENT_SECRET"))
    gid = (data.get("OAUTH_GOOGLE_CLIENT_ID") or "").strip()
    gsec = (data.get("OAUTH_GOOGLE_CLIENT_SECRET") or "").strip()
    if gid or gsec:
        _nonempty("OAUTH_GOOGLE_CLIENT_ID", gid)
        _nonempty("OAUTH_GOOGLE_CLIENT_SECRET", gsec)


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.path).resolve()
    if not path.is_file():
        print(f"ERROR: {path} does not exist.", file=sys.stderr)
        return 1
    data = parse_env_file(path)
    try:
        validate_parsed(data)
    except ValueError as e:
        print(f"ERROR: .env.integration validation failed: {e}", file=sys.stderr)
        return 1
    print(f"OK: {path} passes integration deploy validation.")
    return 0


def cmd_sync_from_env(args: argparse.Namespace) -> int:
    path = Path(args.path).resolve()
    if not path.is_file():
        print(f"ERROR: {path} does not exist.", file=sys.stderr)
        return 1
    data = parse_env_file(path)
    updated = False
    for k in SYNC_FROM_ENV_KEYS:
        v = os.environ.get(k, "").strip()
        if v and data.get(k) != v:
            data[k] = v
            updated = True
    if updated:
        write_dict(path, data)
        print(f"Updated public/gateway keys in {path} from shell environment (no secrets printed).")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write", help="Write .env.integration from DEVNEST_CI_WRITE_* env vars")
    w.set_defaults(func=cmd_write)

    v = sub.add_parser("validate", help="Validate an existing .env.integration file")
    v.add_argument("--path", required=True)
    v.set_defaults(func=cmd_validate)

    s = sub.add_parser(
        "sync-from-env",
        help="Merge SYNC_FROM_ENV_KEYS from os.environ into the file (after deploy-ec2 normalizes URLs)",
    )
    s.add_argument("--path", required=True)
    s.set_defaults(func=cmd_sync_from_env)

    args = p.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
