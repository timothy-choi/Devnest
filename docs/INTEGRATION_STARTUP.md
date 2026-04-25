# Integration and EC2 full-stack startup

This runbook covers `docker-compose.integration.yml`: local smoke with bundled Postgres, CI with RDS, and EC2 via `scripts/deploy-ec2.sh`.

## Quick start (bundled Postgres, same machine as browser)

**`app.lvh.me` default:** fine when the browser runs on the **same host** as Docker (subdomains resolve to `127.0.0.1`). For **remote** users, set `DEVNEST_BASE_DOMAIN` to sslip.io or DNS you control (see EC2 section).

### Compose vs `backend/.env`

For `backend` / `workspace-worker`, Docker Compose **`environment:` overrides `env_file: backend/.env`** for the same variable names. The stack uses `DATABASE_URL` and `DEVNEST_DATABASE_URL` from compose first, so integration containers do not silently pick a different DSN from `backend/.env` when compose injects RDS URLs.

1. Repo root: optional `.env` from `.env.integration.example` (defaults work for local).
2. Run (one command — validates env, starts compose, runs health checks):

   ```bash
   ./scripts/deploy_integration.sh
   ```

   Equivalent manual command:

   ```bash
   docker compose --env-file .env.integration -f docker-compose.integration.yml up -d --build
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
| `OAUTH_GITHUB_CLIENT_ID` / `OAUTH_GITHUB_CLIENT_SECRET` | Required together to enable GitHub OAuth sign-in. Legacy aliases `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` also work. |
| `OAUTH_GOOGLE_CLIENT_ID` / `OAUTH_GOOGLE_CLIENT_SECRET` | Required together to enable Google OAuth sign-in. Legacy aliases `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` also work. |
| `GITHUB_OAUTH_PUBLIC_BASE_URL` / `GCLOUD_OAUTH_PUBLIC_BASE_URL` | Public callback base URLs. If unset or loopback-only, backend will prefer `DEVNEST_FRONTEND_PUBLIC_BASE_URL` when it is a non-loopback public host. |
| `DEVNEST_SNAPSHOT_STORAGE_PROVIDER` | Snapshot/archive storage backend. Keep `local` for single-node dev **when** `DEVNEST_EXPECT_EXTERNAL_POSTGRES` and `DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS` are both false. If either expect flag is `true` (RDS / remote posture), this **must** be `s3` or Settings aborts startup. Live workspace files still stay on `WORKSPACE_PROJECTS_BASE` host bind mounts. |
| `DEVNEST_S3_SNAPSHOT_BUCKET` | S3 bucket for snapshot archives when `DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3`. |
| `DEVNEST_S3_SNAPSHOT_PREFIX` | Object prefix for snapshots (default `devnest-snapshots`). |
| `AWS_REGION` | AWS region for S3 snapshot access. Required when `DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3`. |

### Optional fail-fast flags (backend + workspace-worker)

Set in the environment or compose when you want misconfiguration to **abort at process start** instead of silently using wrong defaults:

| Variable | When to set | Effect |
|----------|-------------|--------|
| `DEVNEST_EXPECT_EXTERNAL_POSTGRES` | `true` when you intend RDS/managed Postgres | `RuntimeError` if the resolved DB host is `postgres` (bundled compose service name). |
| `DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS` | `true` for EC2/remote users | `RuntimeError` if `DEVNEST_BASE_DOMAIN` is `app.lvh.me` or `app.devnest.local`. |

`scripts/deploy-ec2.sh` sets both to `true` automatically when `DATABASE_URL` is set.

When either expect flag is `true`, **snapshot archives must use S3** (`DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3` plus `DEVNEST_S3_SNAPSHOT_BUCKET` and `AWS_REGION`). This keeps the FastAPI process and `workspace-worker` aligned: both read the same Settings-derived provider, and there is no silent fallback to local disk in cloud posture. `deploy-ec2.sh` exits before `docker compose` if `DATABASE_URL` is set but S3 snapshot variables are missing.

### OAuth requirements

GitHub OAuth is considered configured only when all of these are true:

- `OAUTH_GITHUB_CLIENT_ID` or `GITHUB_CLIENT_ID` is set
- `OAUTH_GITHUB_CLIENT_SECRET` or `GITHUB_CLIENT_SECRET` is set
- `GITHUB_OAUTH_PUBLIC_BASE_URL` resolves to a non-empty public callback base

Google OAuth is considered configured only when all of these are true:

- `OAUTH_GOOGLE_CLIENT_ID` or `GOOGLE_CLIENT_ID` is set
- `OAUTH_GOOGLE_CLIENT_SECRET` or `GOOGLE_CLIENT_SECRET` is set
- `GCLOUD_OAUTH_PUBLIC_BASE_URL` resolves to a non-empty public callback base

For integration/EC2, you will usually set only `DEVNEST_FRONTEND_PUBLIC_BASE_URL`; backend startup will
derive both OAuth public base URLs from that value when the explicit OAuth bases are unset or still point
at `localhost`.

### Compose command (EC2)

Prefer the deploy script (git sync, env normalization, compose ordering for RDS):

```bash
scripts/deploy-ec2.sh <branch>
```

Manual equivalent (simplified): export the variables above, then from repo root. When you use a generated
**`.env.integration`** file (recommended for RDS), pass it explicitly so substitution and container env stay
aligned after restarts:

```bash
docker compose --env-file .env.integration -f docker-compose.integration.yml up -d --build
```

Without that file, `docker compose -f docker-compose.integration.yml …` still works for local bundled Postgres
when variables are only in the shell or repo-root `.env`.

When using **external** Postgres, `scripts/deploy-ec2.sh` skips the local `postgres` service and brings up
`route-admin`, then backend/worker/frontend without pulling in the bundled DB. The script always uses
`docker compose --env-file "${REPO_DIR}/.env.integration" …` when that file exists.

### `.env.integration` shape (secrets redacted)

After `write` / `validate`, the file is mode `0600` and values are double-quoted. Typical keys (values shown as placeholders only):

| Key | Example shape |
|-----|----------------|
| `DATABASE_URL` | `postgresql+psycopg://USER:***@HOST:5432/DBNAME?sslmode=require` |
| `DEVNEST_COMPOSE_DATABASE_URL` | Same as `DATABASE_URL` |
| `DEVNEST_DATABASE_URL` | Same as `DATABASE_URL` |
| `DEVNEST_EXPECT_EXTERNAL_POSTGRES` | `true` (written by `write`; required by `validate` for RDS) |
| `DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS` | `true` (same) |
| `DEVNEST_BASE_DOMAIN` | `98-92-163-201.sslip.io` (no scheme) |
| `DEVNEST_GATEWAY_PUBLIC_SCHEME` / `DEVNEST_GATEWAY_PUBLIC_PORT` | `http` / `9081` |
| `DEVNEST_FRONTEND_PUBLIC_BASE_URL` | `http://…:3000` |
| `NEXT_PUBLIC_APP_BASE_URL` / `NEXT_PUBLIC_API_BASE_URL` | `http://…:3000` and `http://…:8000` |
| `GITHUB_OAUTH_PUBLIC_BASE_URL` / `GCLOUD_OAUTH_PUBLIC_BASE_URL` | Same origin as UI for callbacks |
| `OAUTH_GITHUB_CLIENT_ID` / `OAUTH_GITHUB_CLIENT_SECRET` | Set in CI from GitHub secrets |
| `OAUTH_GOOGLE_*` | Optional; omitted when both empty |
| `DEVNEST_SNAPSHOT_STORAGE_PROVIDER` | `s3` for RDS |
| `DEVNEST_S3_SNAPSHOT_BUCKET` / `DEVNEST_S3_SNAPSHOT_PREFIX` / `AWS_REGION` | Bucket, prefix, region |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Optional when using an IAM instance profile |

Safe summary (no secrets):

```bash
python3 scripts/write_integration_deploy_env.py diagnostics --path .env.integration
```

### Post-deploy verification (EC2)

From the repo root on the instance (after `scripts/deploy-ec2.sh`):

```bash
python3 scripts/write_integration_deploy_env.py validate --path .env.integration
python3 scripts/write_integration_deploy_env.py diagnostics --path .env.integration
curl -fsS "http://127.0.0.1:8000/ready"
curl -fsS "http://127.0.0.1:3000/" >/dev/null   # or open :3000 in a browser
docker compose --env-file .env.integration -f docker-compose.integration.yml ps
```

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

# Optional but required if you want OAuth sign-in enabled.
# export OAUTH_GITHUB_CLIENT_ID='...'
# export OAUTH_GITHUB_CLIENT_SECRET='...'
# export OAUTH_GOOGLE_CLIENT_ID='...'
# export OAUTH_GOOGLE_CLIENT_SECRET='...'
# Optional explicit callback overrides; otherwise backend will reuse DEVNEST_FRONTEND_PUBLIC_BASE_URL:
# export GITHUB_OAUTH_PUBLIC_BASE_URL="http://${PUBLIC_HOST}:3000"
# export GCLOUD_OAUTH_PUBLIC_BASE_URL="http://${PUBLIC_HOST}:3000"

# Snapshot archives only: use S3 for create/restore/delete/existence checks.
# Active workspace files still stay on /var/lib/devnest/workspace-projects bind mounts.
export DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3
export DEVNEST_S3_SNAPSHOT_BUCKET='your-devnest-snapshots-bucket'
export DEVNEST_S3_SNAPSHOT_PREFIX='devnest-snapshots'
export AWS_REGION='us-east-1'
# Optional when not using an instance profile:
# export AWS_ACCESS_KEY_ID='...'
# export AWS_SECRET_ACCESS_KEY='...'

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
docker compose --env-file .env.integration -f docker-compose.integration.yml exec -T backend python -c "from app.libs.common.config import format_database_url_for_log, get_settings; print(format_database_url_for_log(get_settings().database_url))"
```

(If you do not use `.env.integration`, omit `--env-file .env.integration`.)

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

## Snapshot S3 verification

### Startup diagnostics

Both `backend` and `workspace-worker` now log snapshot storage configuration on startup without secrets:

- `provider`
- `bucket`
- `prefix`
- `region`

Expected log pattern:

```text
[DevNest diagnostics] API startup snapshot_storage provider=s3 bucket=... prefix=... region=... root=-
[DevNest diagnostics] workspace-worker startup snapshot_storage provider=s3 bucket=... prefix=... region=... root=-
```

For `provider=local`, the same log line shape applies: `bucket`, `prefix`, and `region` are logged as `-`, and `root` is the resolved filesystem path for archives.

If `DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3` is set without `DEVNEST_S3_SNAPSHOT_BUCKET` or `AWS_REGION`, process startup fails immediately with a clear `RuntimeError`. If either `DEVNEST_EXPECT_EXTERNAL_POSTGRES` or `DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS` is `true` but the provider is not `s3`, startup also fails with an explicit `RuntimeError`.

### Verify backend and worker see the same snapshot fields (no secrets)

After `up`, both containers should print identical `provider` / `bucket` / `prefix` / `region` / `root` from `snapshot_storage_log_fields()` (values come from the same compose anchor + `.env`):

```bash
docker compose -f docker-compose.integration.yml exec -T backend \
  python -c "from app.services.storage.factory import snapshot_storage_log_fields; print(snapshot_storage_log_fields())"
docker compose -f docker-compose.integration.yml exec -T workspace-worker \
  python -c "from app.services.storage.factory import snapshot_storage_log_fields; print(snapshot_storage_log_fields())"
```

### Manual verification commands

Set a token and base URL once:

```bash
export API_BASE_URL="http://${PUBLIC_HOST}:8000"
export AUTH_TOKEN='replace-with-a-real-bearer-token'
```

1. Create workspace:

```bash
curl -sS -X POST "${API_BASE_URL}/workspaces" \
  -H "Authorization: Bearer ${AUTH_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"name":"snapshot-s3-check"}'
```

2. Create snapshot:

```bash
export WORKSPACE_ID='<workspace-id>'

curl -sS "${API_BASE_URL}/workspaces/${WORKSPACE_ID}" \
  -H "Authorization: Bearer ${AUTH_TOKEN}"

# Wait until the workspace status is RUNNING or STOPPED before snapshotting.

docker compose -f docker-compose.integration.yml exec -T backend \
  sh -lc "mkdir -p /var/lib/devnest/workspace-projects/${WORKSPACE_ID} && printf 'before-snapshot\n' > /var/lib/devnest/workspace-projects/${WORKSPACE_ID}/snapshot-proof.txt"

curl -sS -X POST "${API_BASE_URL}/workspaces/${WORKSPACE_ID}/snapshots" \
  -H "Authorization: Bearer ${AUTH_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"name":"pre-restore"}'
```

3. Verify object exists in S3:

```bash
export SNAPSHOT_ID='<snapshot-id-from-response>'

until curl -fsS "${API_BASE_URL}/snapshots/${SNAPSHOT_ID}" \
  -H "Authorization: Bearer ${AUTH_TOKEN}" | grep -q '"status":"AVAILABLE"'; do
  sleep 2
done

aws s3api head-object \
  --bucket "${DEVNEST_S3_SNAPSHOT_BUCKET}" \
  --key "${DEVNEST_S3_SNAPSHOT_PREFIX}/ws-${WORKSPACE_ID}/snapshot-${SNAPSHOT_ID}.tar.gz" \
  --region "${AWS_REGION}"
```

4. Restore snapshot:

```bash
curl -sS -X POST "${API_BASE_URL}/workspaces/stop/${WORKSPACE_ID}" \
  -H "Authorization: Bearer ${AUTH_TOKEN}"

docker compose -f docker-compose.integration.yml exec -T backend \
  sh -lc "printf 'after-snapshot-change\n' > /var/lib/devnest/workspace-projects/${WORKSPACE_ID}/snapshot-proof.txt"

curl -sS -X POST "${API_BASE_URL}/snapshots/${SNAPSHOT_ID}/restore" \
  -H "Authorization: Bearer ${AUTH_TOKEN}"

# Wait until the snapshot status returns to AVAILABLE after the restore job completes.
until curl -fsS "${API_BASE_URL}/snapshots/${SNAPSHOT_ID}" \
  -H "Authorization: Bearer ${AUTH_TOKEN}" | grep -q '"status":"AVAILABLE"'; do
  sleep 2
done
```

5. Verify restore worked:

```bash
docker compose -f docker-compose.integration.yml exec -T backend \
  sh -lc "cat /var/lib/devnest/workspace-projects/${WORKSPACE_ID}/snapshot-proof.txt"

curl -sS "${API_BASE_URL}/workspaces/${WORKSPACE_ID}/snapshots" \
  -H "Authorization: Bearer ${AUTH_TOKEN}"
```

The practical check is:

- snapshot row returns to `AVAILABLE`
- restore job completes successfully
- `/var/lib/devnest/workspace-projects/${WORKSPACE_ID}/snapshot-proof.txt` is back to `before-snapshot`

## OAuth and Workspace URL startup diagnostics

Backend and worker startup logs now emit the effective public URL configuration without secrets:

```text
[DevNest diagnostics] API startup frontend_public_base_url=http://203-0-113-10.sslip.io:3000 github_oauth_public_base_url=http://203-0-113-10.sslip.io:3000 gcloud_oauth_public_base_url=http://203-0-113-10.sslip.io:3000 github_oauth_configured=true google_oauth_configured=false
[DevNest diagnostics] API startup gateway devnest_base_domain=203-0-113-10.sslip.io public_scheme=http public_port=0 gateway_enabled=true route_admin_url=http://route-admin:8080
```

Use these to verify:

- OAuth is not “configured” unless both credentials and callback base URL are present.
- Remote workspace URLs come from `DEVNEST_BASE_DOMAIN`, `DEVNEST_GATEWAY_PUBLIC_SCHEME`, and `DEVNEST_GATEWAY_PUBLIC_PORT`.
- `app.lvh.me` is only safe for same-host local browsing, never for EC2 remote clients.
