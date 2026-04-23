# Integration and EC2 full-stack startup

This runbook covers `docker-compose.integration.yml`: local smoke with bundled Postgres, CI with RDS, and EC2 via `scripts/deploy-ec2.sh`.

## Quick start (bundled Postgres, same machine as browser)

**`app.lvh.me` default:** fine when the browser runs on the **same host** as Docker (subdomains resolve to `127.0.0.1`). For **remote** users, set `DEVNEST_BASE_DOMAIN` to sslip.io or DNS you control (see EC2 section).

### Compose vs `backend/.env`

For `backend` / `workspace-worker`, Docker Compose **`environment:` overrides `env_file: backend/.env`** for the same variable names. The stack uses `DATABASE_URL` and `DEVNEST_DATABASE_URL` from compose first, so integration containers do not silently pick a different DSN from `backend/.env` when compose injects RDS URLs.

1. Repo root: optional `.env` from `.env.integration.example` (defaults work for local).
2. Run:

   ```bash
   docker compose -f docker-compose.integration.yml up -d --build
   ```

3. Wait for `backend` health (uses `GET /ready`, which checks database connectivity after Alembic migrations).

## EC2 / remote browsers (RDS + public DNS)

### Required environment (host / CI before `compose up`)

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` or `DEVNEST_COMPOSE_DATABASE_URL` | `postgresql+psycopg://…` DSN for RDS or managed Postgres. Compose copies this into both `DATABASE_URL` and `DEVNEST_DATABASE_URL` in backend/worker containers so the API, worker, and Alembic agree. |
| `DEVNEST_BASE_DOMAIN` | Wildcard DNS base for workspace URLs (`ws-<id>.<domain>`). Must resolve to the host running Traefik for **remote** clients. Do **not** use `app.lvh.me` for remote users (it resolves to the client’s loopback). `deploy-ec2.sh` can derive `<dashed-ip>.sslip.io` when unset on EC2. |
| `DEVNEST_FRONTEND_PUBLIC_BASE_URL` | Browser-visible UI origin (scheme + host + port), used for OAuth redirects and `NEXT_PUBLIC_APP_BASE_URL` in the frontend image. |
| `NEXT_PUBLIC_API_BASE_URL` | Browser → FastAPI origin (often `:8000` on the same host as the API). `deploy-ec2.sh` derives this from `DEVNEST_FRONTEND_PUBLIC_BASE_URL` when unset. |
| `DEVNEST_GATEWAY_PORT` / `DEVNEST_GATEWAY_PUBLIC_PORT` | Published Traefik HTTP port on the host and the port embedded in `gateway_url` when non-default (see compose header comments). |

### Optional fail-fast flags (backend + workspace-worker)

Set in the environment or compose when you want misconfiguration to **abort at process start** instead of silently using wrong defaults:

| Variable | When to set | Effect |
|----------|-------------|--------|
| `DEVNEST_EXPECT_EXTERNAL_POSTGRES` | `true` when you intend RDS/managed Postgres | `RuntimeError` if the resolved DB host is `postgres` (bundled compose service name). |
| `DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS` | `true` for EC2/remote users | `RuntimeError` if `DEVNEST_BASE_DOMAIN` is `app.lvh.me` or `app.devnest.local`. |

`scripts/deploy-ec2.sh` sets both to `true` automatically when `DATABASE_URL` is set.

### Compose command (EC2)

Prefer the deploy script (git sync, env normalization, compose ordering for RDS):

```bash
scripts/deploy-ec2.sh <branch>
```

Manual equivalent (simplified): export the variables above, then from repo root:

```bash
docker compose -f docker-compose.integration.yml up -d --build
```

When using **external** Postgres, the deploy script skips the local `postgres` service and brings up `route-admin`, then backend/worker/frontend without pulling in the bundled DB.

## Frontend → backend (Next server routes)

- **Inside the frontend container:** `INTERNAL_API_BASE_URL` (default `http://backend:8000`) or see `frontend/lib/server/internal-api-base.ts`.
- **Local `next dev` on the host:** use `NEXT_PUBLIC_API_BASE_URL` pointing at a reachable API (not the hostname `backend` unless defined in DNS).

## Verify database target

1. **Backend logs** on API start: `[DevNest diagnostics] API startup database_host=… database_name=…`.
2. **Alembic** (same process as `uvicorn` in compose): `alembic/env.py` logs `Alembic effective DB target: driver=… host=…` (no passwords).
3. **Workspace worker logs** first poll tick: `[DevNest diagnostics] workspace-worker startup DB=… base_domain=…`.
4. **Optional:** `DEVNEST_AUTH_DIAGNOSTICS=true` on backend and frontend, then `GET /api/internal/devnest-diagnostics` (see `frontend/.env.example`).

## Verify gateway / workspace URLs

1. **Backend logs:** `[DevNest diagnostics] API startup gateway devnest_base_domain=… public_scheme=… public_port=… gateway_enabled=… route_admin_url=…`.
2. Open a workspace from the UI; the IDE URL should be `{DEVNEST_GATEWAY_PUBLIC_SCHEME}://ws-<id>.<DEVNEST_BASE_DOMAIN>[:port]/` with a host that resolves to your Traefik instance from the **browser’s** network.

## Verify API readiness

- `curl -sf http://<host>:8000/ready` — includes DB (and optional Redis when configured). The integration compose **backend healthcheck** uses `/ready` so dependents start only after migrations and DB connectivity succeed.

## Intentionally deferred

- Optional second compose file that **requires** `DEVNEST_COMPOSE_DATABASE_URL` at parse time for RDS-only flows (would duplicate the fail-fast flags above).
- Traefik `depends_on: backend: healthy` would order startup more strictly but conflicts with `deploy-ec2.sh` bringing Traefik up before the API; recycle Traefik after backend if ForwardAuth errors appear briefly.

---

## G. One-shot EC2 / remote startup (copy-paste)

Run from a **fresh shell** on the EC2 host (or any Linux host with Docker), at the **repository root** after `git clone`. Replace the three placeholders: `RDS_URL`, `PUBLIC_HOST` (hostname clients use to reach this machine—**sslip.io**, DNS name, or public DNS), and optionally adjust gateway ports.

```bash
set -euo pipefail
cd ~/Devnest   # <-- change to your clone path

# --- placeholders (edit these) ---
export RDS_URL='postgresql+psycopg://USER:PASSWORD@db.xxxxx.us-east-1.rds.amazonaws.com:5432/devnest?sslmode=require'
export PUBLIC_HOST='203-0-113-10.sslip.io'   # must resolve to this host for remote browsers; see deploy-ec2.sh for EC2 auto-derivation

# --- required wiring (same semantics as scripts/deploy-ec2.sh when using RDS) ---
export DATABASE_URL="$RDS_URL"
export DEVNEST_COMPOSE_DATABASE_URL="$RDS_URL"
export DEVNEST_EXPECT_EXTERNAL_POSTGRES=true
export DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS=true
export DEVNEST_BASE_DOMAIN="$PUBLIC_HOST"
export DEVNEST_FRONTEND_PUBLIC_BASE_URL="http://${PUBLIC_HOST}:3000"
export NEXT_PUBLIC_API_BASE_URL="http://${PUBLIC_HOST}:8000"
export JWT_SECRET_KEY="${JWT_SECRET_KEY:-$(openssl rand -hex 32)}"

# Traefik on host port 80; omit :port in generated workspace URLs (typical EC2)
export DEVNEST_GATEWAY_PORT="${DEVNEST_GATEWAY_PORT:-80}"
export DEVNEST_GATEWAY_PUBLIC_PORT="${DEVNEST_GATEWAY_PUBLIC_PORT:-0}"

COMPOSE=(docker compose -f docker-compose.integration.yml)

"${COMPOSE[@]}" build workspace-image
"${COMPOSE[@]}" up -d route-admin
"${COMPOSE[@]}" up -d traefik
"${COMPOSE[@]}" up -d --build --force-recreate --no-deps backend
"${COMPOSE[@]}" up -d --build --force-recreate --no-deps workspace-worker
"${COMPOSE[@]}" up -d --build --force-recreate --no-deps frontend
"${COMPOSE[@]}" ps
```

**Equivalent with the deploy script** (after the same `export` block, or with variables in repo-root `.env`): `./scripts/deploy-ec2.sh <branch>` — it applies the same `DATABASE_URL` / sslip normalization and the correct `docker compose` ordering for external Postgres.

### One command: verify backend DB target (no secrets)

```bash
docker compose -f docker-compose.integration.yml exec -T backend python -c "from app.libs.common.config import format_database_url_for_log, get_settings; print(format_database_url_for_log(get_settings().database_url))"
```

Expect `driver=… host=<your RDS endpoint> database=…` (no password in output).

### One command: verify the frontend container can reach FastAPI

```bash
docker compose -f docker-compose.integration.yml exec -T frontend node -e "fetch('http://backend:8000/ready').then(async r=>{const t=await r.text();if(!r.ok)throw new Error('HTTP '+r.status);console.log(t)}).catch(e=>{console.error(e);process.exit(1)})"
```

Expect JSON like `{"status":"ready",...}` (HTTP 200). This uses the same `http://backend:8000` path Next server routes use (`INTERNAL_API_BASE_URL`).

### One command: verify workspace URL generation inputs

```bash
docker compose -f docker-compose.integration.yml exec -T backend python -c "from app.libs.common.config import get_settings; s=get_settings(); p=s.devnest_gateway_public_port; suf=(':'+str(p)) if p else ''; print(s.devnest_gateway_public_scheme+'://ws-<workspace_id>.'+s.devnest_base_domain+suf+'/')"
```

Expect something like `http://ws-<workspace_id>.203-0-113-10.sslip.io/` (or with `:9081` if `DEVNEST_GATEWAY_PUBLIC_PORT` is non-zero). Confirm `ws-<id>.<DEVNEST_BASE_DOMAIN>` resolves to Traefik from a **remote** client (`dig +short ws-1.<domain>` or open in browser after creating a workspace).
