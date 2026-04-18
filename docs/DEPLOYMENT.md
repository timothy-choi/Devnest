# DevNest Deployment Guide

## Prerequisites

| Requirement | Minimum Version | Notes |
|---|---|---|
| Python | 3.11 | Required for the FastAPI backend |
| PostgreSQL | 15 | Production database |
| Docker | 24.0 | Workspace container orchestration |
| Alembic | 1.13+ | Schema migrations (included in `requirements.txt`) |

---

## Deployment profiles

These are **intended shapes**, not separate products. Pick the combination that matches where Docker and the topology layer run versus where Postgres/Redis and the API live.

| Profile | Workspace Docker + Linux bridge / topology | API / worker | Backing services |
|--------|---------------------------------------------|--------------|------------------|
| **Local Docker (development)** | Same host as API; `local_docker` execution node | Same process or local worker | Local Postgres/Redis or Docker Compose |
| **EC2 / VM execution** | One or more **instances** run Docker + `DbTopologyAdapter` Linux wiring (bridge/veth on the instance) | Control plane can be separate; workers must use `ssh_docker` or `ssm_docker` so runtime and `topology_command_runner` target the instance | RDS/ElastiCache (or self-managed) Postgres/Redis; object storage for snapshots (e.g. S3) |
| **Cloud-backed data plane** | Unchanged vs EC2 profile — still **node-local** topology | Same | Prefer managed Postgres/Redis and S3-compatible snapshot storage instead of colocated containers |
| **Future: ECS task–style runtimes** | **Not required** for the EC2 profile. Task ENI / Service Connect models differ from host bridge + netns; migrating would mean a new runtime/topology binding, not a mandate to drop the current adapter today. | TBD | Same managed-store direction |

**Probes:** For EC2/SSH/SSM nodes, `NodeExecutionBundle.service_reachability_runner` runs TCP (`nc`) and HTTP (`curl`) **on the execution host**, so readiness matches real reachability. Set `DEVNEST_PROBE_ASSUME_COLOCATED_ENGINE=false` on any control-plane host that builds an orchestrator **without** that runner (so misconfiguration fails closed). Local dev keeps the default `true`.

**Failed bring-up:** The orchestrator performs a **compensating rollback** (detach, stop container, release IP lease) when bring-up raises or when the health probe fails, with bounded retries on the inner stop; the worker may set `WorkspaceRuntime.health_status=CLEANUP_REQUIRED` if rollback still fails. `RECONCILE_RUNTIME` runs a **topology janitor** first (stuck attachments, orphan IP leases, simple DB/workspace drift) when `DEVNEST_TOPOLOGY_JANITOR_ENABLED=true` (default), then applies reconcile logic. PostgreSQL workers use a **session advisory lock** per workspace during reconcile to avoid duplicate repairs.

**Readiness:** Set `DEVNEST_WORKSPACE_IDE_HEALTH_PATH` (default `/healthz`) for code-server HTTP readiness after TCP succeeds. Use `DEVNEST_WORKSPACE_HTTP_PROBE_ENABLED` and `DEVNEST_PROBE_ASSUME_COLOCATED_ENGINE` per the EC2 control-plane vs execution-host split above.

**Quota:** `CREATE` and start-class intents enforce `max_running_workspaces`, **monthly** `max_runtime_hours` (from `WORKSPACE_STOPPED` usage quantities in seconds), and **`max_cpu` / `max_memory_mb`** against summed `WorkspaceRuntime.reserved_*` plus the workspace being started.

---

## Production Deployment Checklist

### 1. Environment Variables

Set the following before starting the application:

```bash
# Required
DATABASE_URL=postgresql+psycopg://devnest:STRONG_PASSWORD@db-host:5432/devnest
JWT_SECRET_KEY=$(openssl rand -hex 32)

# Runtime environment — triggers automatic secret enforcement for non-development environments.
# Accepted values: development (default), staging, production.
# When set to staging or production and JWT_SECRET_KEY is the default placeholder, startup aborts.
DEVNEST_ENV=production

# Explicit secret enforcement (alternative to DEVNEST_ENV; either flag is sufficient).
DEVNEST_REQUIRE_SECRETS=true

# Internal API keys (use different keys per scope)
INTERNAL_API_KEY=<random 32+ char string>
INTERNAL_API_KEY_WORKSPACE_JOBS=<different random string>
INTERNAL_API_KEY_WORKSPACE_RECONCILE=<different random string>
INTERNAL_API_KEY_AUTOSCALER=<different random string>
INTERNAL_API_KEY_INFRASTRUCTURE=<different random string>
DEVNEST_INTERNAL_API_KEY_MIN_LENGTH=24   # enforce minimum key length

# Worker (choose one of the three worker modes — see below)
DEVNEST_WORKER_ENABLED=true              # in-process worker
DEVNEST_WORKER_POLL_INTERVAL_SECONDS=5
DEVNEST_WORKER_BATCH_SIZE=5

# Stuck-job reclaim (worker lifecycle hardening)
# Jobs stuck in RUNNING longer than this many seconds are retried or failed-terminal.
WORKSPACE_JOB_STUCK_TIMEOUT_SECONDS=300  # 0 = disable

# Automated reconcile loop
DEVNEST_RECONCILE_ENABLED=true
DEVNEST_RECONCILE_INTERVAL_SECONDS=30
DEVNEST_RECONCILE_BATCH_SIZE=10
DEVNEST_RECONCILE_TARGET_STATUSES=RUNNING,ERROR
DEVNEST_RECONCILE_LEASE_TTL_SECONDS=120  # seconds before a RUNNING reconcile is stale

# Rate limiting — choose memory (single-process) or redis (distributed multi-worker)
DEVNEST_RATE_LIMIT_ENABLED=true
DEVNEST_RATE_LIMIT_AUTH_PER_MINUTE=20   # /auth/login, /auth/register, /auth/forgot-password
DEVNEST_RATE_LIMIT_SSE_PER_MINUTE=30    # /workspaces/{id}/events SSE endpoint

# Distributed rate limiting (Redis-backed) — set for multi-worker deployments
# DEVNEST_RATE_LIMIT_BACKEND=redis              # "memory" (default) or "redis"
# DEVNEST_REDIS_URL=redis://redis-host:6379/0   # required when backend=redis
# DEVNEST_REQUIRE_DISTRIBUTED_RATE_LIMITING=true # abort if redis URL missing

# Integration / Provider Token Encryption
# Generate with: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
DEVNEST_TOKEN_ENCRYPTION_KEY=<fernet-key>

# GitHub OAuth (sign-in + repo connect)
GITHUB_CLIENT_ID=<your-github-client-id>
GITHUB_CLIENT_SECRET=<your-github-client-secret>
GITHUB_OAUTH_PUBLIC_BASE_URL=https://api.yourdomain.com   # base for /auth/oauth/github/callback

# Google OAuth (sign-in only in V1)
GOOGLE_CLIENT_ID=<your-google-client-id>
GOOGLE_CLIENT_SECRET=<your-google-client-secret>
GCLOUD_OAUTH_PUBLIC_BASE_URL=https://api.yourdomain.com   # base for /auth/oauth/google/callback

# Workspace runtime: projects base (required for persistent workspace files)
# Must be an absolute path on the Docker host; created automatically if missing.
DEVNEST_WORKSPACE_PROJECTS_BASE=/data/devnest-workspaces
# code-server image (must include code-server; official: codercom/code-server:latest)
DEVNEST_WORKSPACE_IMAGE=codercom/code-server:latest

# After TCP connect on the workspace IDE port, perform HTTP GET to confirm the IDE is serving (code-server readiness).
# Staging/production: must stay true together with DEVNEST_REQUIRE_IDE_HTTP_PROBE (startup validates both).
DEVNEST_WORKSPACE_HTTP_PROBE_ENABLED=true
# Staging/production: must be true so RUNNING implies HTTP IDE readiness (not TCP-only).
DEVNEST_REQUIRE_IDE_HTTP_PROBE=true

# Authoritative placement: never enable in staging/production (EC2/VM multi-node).
DEVNEST_ALLOW_RUNTIME_ENV_FALLBACK=false
# Each schedulable execution node must have default_topology_id set for new workload placement
# (strict mode forbids inferring topology from DEVNEST_TOPOLOGY_ID when scheduling).

# Reconcile duplicate-suppression (requires PostgreSQL URL in staging/production).
DEVNEST_RECONCILE_LOCK_BACKEND=postgres_advisory
DEVNEST_REQUIRE_PROD_RECONCILE_LOCKING=true
# When false, TCP probes from this process require service_reachability_runner (execution node). Use on API-only hosts.
# Default true for local/dev and workers that are co-located with Docker.
DEVNEST_PROBE_ASSUME_COLOCATED_ENGINE=true

# SSE: max latency for cross-worker event delivery (DB poll fallback when not on same gunicorn worker)
DEVNEST_SSE_POLL_INTERVAL_SECONDS=2

# Autoscaler drain delay (safe scale-down)
DEVNEST_AUTOSCALER_DRAIN_DELAY_SECONDS=30         # wait before terminating a draining node
DEVNEST_AUTOSCALER_RECENT_ACTIVITY_WINDOW_SECONDS=300  # skip nodes with recent heartbeats

# Terminal WebSocket settings
DEVNEST_WORKSPACE_SHELL=/bin/bash   # shell to launch in terminal sessions
DEVNEST_TERMINAL_DEFAULT_COLS=200
DEVNEST_TERMINAL_DEFAULT_ROWS=50

# Gateway (optional; enable after Traefik sidecar is running)
DEVNEST_GATEWAY_ENABLED=true
DEVNEST_GATEWAY_URL=http://route-admin:9080
DEVNEST_BASE_DOMAIN=app.yourdomain.com

# Gateway ForwardAuth (enable once TLS and session flows are tested)
DEVNEST_GATEWAY_AUTH_ENABLED=true

# Snapshot storage (S3 for production; local for dev)
DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3
DEVNEST_S3_SNAPSHOT_BUCKET=your-devnest-snapshots-bucket
DEVNEST_S3_SNAPSHOT_PREFIX=devnest-snapshots
# Leave AWS keys empty to use IAM instance profile (recommended)
# AWS_ACCESS_KEY_ID=...
# AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
```

### 2. Database Migration

Always run migrations **before** starting the application:

```bash
cd backend
alembic upgrade head
```

**First-time setup on a fresh database:**

```bash
alembic upgrade head   # creates all tables from revision 0001 onward
```

**Upgrading an existing database (previously using `create_all`):**

```bash
# Mark baseline as applied (tables already exist)
alembic stamp 0001

# Apply all incremental migrations
alembic upgrade head
```

### 3. Start the API

```bash
cd backend

# Production: use gunicorn with uvicorn workers
gunicorn app.main:app \
  -k uvicorn.workers.UvicornWorker \
  --workers 4 \
  --bind 0.0.0.0:8000 \
  --access-logfile - \
  --error-logfile -

# Or with uvicorn directly (single process)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 3b. Production Docker

A production-like stack is available in `docker-compose.prod.yml`:

```bash
# Build backend image
docker build -t devnest-backend:latest ./backend

# Run migrations
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head

# Start all services
docker compose -f docker-compose.prod.yml up -d
```

The `docker-compose.prod.yml` includes:
- `postgres` (PostgreSQL 15) with healthcheck
- `api` (FastAPI + in-process worker + reconcile loop)

### 4. Worker Configuration

Choose the job worker mode that fits your deployment:

#### Option A: In-process worker (simple single-process deployments)

```bash
DEVNEST_WORKER_ENABLED=true
```

The worker starts automatically with the FastAPI process, runs stuck-job reclaim on every tick, and shuts down gracefully with it.

#### Option B: Standalone worker process (recommended for production)

Run as a separate process for independent scaling and fault isolation:

```bash
cd backend
python -m app.workers.workspace_job_poll_loop \
  --poll-interval 2 \
  --jobs-per-tick 5 \
  --log-level INFO
```

Use a process supervisor (systemd, Supervisor, or Kubernetes Deployment) with `SIGTERM` for graceful shutdown.

#### Option C: External trigger (cron or HTTP)

For minimal deployments where workspace operations are infrequent:

```bash
# From cron or an external scheduler
curl -X POST http://api-host:8000/internal/workspace-jobs/process \
  -H "X-Internal-API-Key: ${INTERNAL_API_KEY_WORKSPACE_JOBS}" \
  -d '{"limit": 10}'
```

### 5. Automated Reconcile Loop

The reconcile loop runs as a background task inside the FastAPI process alongside the
in-process worker. It enqueues `RECONCILE_RUNTIME` jobs for workspaces in the target
statuses on a configurable cadence.

Enable with:

```bash
DEVNEST_RECONCILE_ENABLED=true
DEVNEST_RECONCILE_INTERVAL_SECONDS=30
```

The reconcile lease (`DEVNEST_RECONCILE_LEASE_TTL_SECONDS=120`) prevents duplicate
reconcile jobs: if a reconcile job is already QUEUED or recently RUNNING for a workspace,
the new enqueue is skipped silently. A RUNNING reconcile older than the TTL is considered
stale (crashed worker) and a new one is allowed.

### 6. Rate Limiting

The in-process rate limiter (`DEVNEST_RATE_LIMIT_ENABLED=true`) applies sliding-window
per-IP limits:

| Endpoint | Limit |
|---|---|
| All routes (global default) | 300 req/min per IP |
| `/auth/login`, `/auth/register`, `/auth/forgot-password` | 20 req/min per IP |
| `/workspaces/{id}/events` (SSE) | 30 req/min per IP |

Responses exceeding the limit receive HTTP 429 with `Retry-After`. To disable in dev:

```bash
DEVNEST_RATE_LIMIT_ENABLED=false
```

---

## Gateway: TLS and ForwardAuth

### TLS / HTTPS

TLS is handled at the Traefik gateway layer.

**Local / dev (self-signed):**

```bash
# devnest-gateway/.env
DEVNEST_TLS_ENABLED=true
DEVNEST_GATEWAY_TLS_PORT=443
```

Traefik automatically generates a self-signed certificate for the `websecure` (`:443`) entrypoint. No additional config is needed. Your browser will show a cert warning — accept it.

**Production (Let's Encrypt):**

1. Ensure port 443 is publicly reachable on your domain.
2. Uncomment and configure `certificatesResolvers` in `devnest-gateway/traefik/traefik.yml`:

```yaml
certificatesResolvers:
  letsencrypt:
    acme:
      email: "you@example.com"
      storage: /etc/traefik/acme/acme.json
      httpChallenge:
        entryPoint: web
```

3. Mount the acme volume in `docker-compose.yml` (uncomment the relevant line).
4. Set `DEVNEST_TLS_ENABLED=true` and `DEVNEST_ACME_EMAIL=you@example.com` in `.env`.
5. Update workspace routers to use `tls.certResolver: letsencrypt`.

### Gateway ForwardAuth

ForwardAuth enforces workspace session validation at the Traefik edge. Enable it after your TLS and session flows are tested in staging.

**Steps:**

1. **Backend**: Set `DEVNEST_GATEWAY_AUTH_ENABLED=true` in the backend `.env`.
2. **Route-admin**: Set `DEVNEST_GATEWAY_AUTH_ENABLED=true` in the gateway `.env`.
3. **Auth URL**: Set `DEVNEST_BACKEND_AUTH_URL=https://api.yourdomain.com/internal/gateway/auth` so Traefik can reach the backend ForwardAuth endpoint.
4. The `devnest-workspace-auth` middleware is defined in `traefik/dynamic/000-base.yml` and is automatically attached to all workspace routes registered by route-admin.

**How it works:**

```
Client → Traefik (websecure :443)
           ↓
     ForwardAuth: GET /internal/gateway/auth
           ↓ (sends original headers including X-Forwarded-Host + X-DevNest-Workspace-Session)
     Backend validates:
       - workspace_id from X-Forwarded-Host (ws-{id}.{base_domain})
       - session token from X-DevNest-Workspace-Session
       - session ACTIVE + not expired
       - workspace RUNNING
           ↓ 200 → Traefik proxies to workspace upstream
             401 → Traefik returns 401 to client
```

**Local dev bypass**: `DEVNEST_GATEWAY_AUTH_ENABLED=false` (default) makes the endpoint return 200 unconditionally, so local stacks work without session tokens.

---

## Docker Compose (Development)

```bash
# Start the full local stack
docker compose -f docker-compose.dev.yml up -d

# Apply migrations after Postgres is ready
docker compose -f docker-compose.dev.yml exec backend alembic upgrade head
```

---

## CI/CD (GitHub Actions)

The CI pipeline (`.github/workflows/tests.yml`) runs on **every push to any branch**, **every pull request**, and **`workflow_dispatch`**. It runs quality checks, backend unit/integration/system tests, gateway tests, frontend checks, and a **Linux full-stack smoke** job (`docker-compose.integration.yml` on the runner).

### EC2: automatic deploy after tests

The same instance is used for **staging** (non-`main` branches) and **production** (`main`): the latest code that passed CI is checked out on the host and the integration compose stack is rebuilt. No manual SSH is required once secrets and the instance are configured.

| Event | Tests | Deploy |
|-------|--------|--------|
| `push` to any branch except `main` | Yes | **Staging** — syncs `origin/<branch>` and `docker compose ... up -d --build` |
| `push` or merge to `main` | Yes | **Production** — syncs `origin/main` |
| `pull_request` | Yes | **No deploy** (PRs never run deploy jobs) |
| `workflow_dispatch` | Yes | **Staging** if the selected ref is not `main`, else **production** |

**Required repository secrets** (Settings → Secrets and variables → Actions):

| Secret | Purpose |
|--------|---------|
| `EC2_HOST` | Public DNS or IPv4 of the instance |
| `EC2_USER` | SSH user (e.g. `ubuntu`, `ec2-user`) |
| `EC2_SSH_KEY` | **Private** key material (full PEM / OpenSSH block), not `.ppk` |

If any of these is empty, deploy steps are skipped; the workflow still passes if tests succeed.

**Remote behavior:** CI uses `appleboy/ssh-action@v1.2.3` (no unsupported inputs) and runs `scripts/deploy-ec2.sh` on the server. That script clones or updates `~/Devnest`, **checks out a branch by name** (`git reset --hard origin/<branch>`), sets `NEXT_PUBLIC_API_BASE_URL` from the workflow for the UI build, runs `docker compose -f docker-compose.integration.yml down` / `up -d --build --force-recreate`, then prints **git status**, **HEAD**, **compose ps**, and **compose logs** for debugging.

#### Integration stack: Linux topology and `pid: host` (EC2 / staging)

Workspace bring-up runs **veth + netns** commands (`ip link set … netns <pid>`, `nsenter -n -t <pid>`) inside the **control-plane** process. Docker reports **`State.Pid`** in the **host** PID namespace for each workspace container.

If the API or worker runs in a **normal** container with an **isolated** PID namespace, that host PID is **not visible** under `/proc/<pid>` to `ip`/`nsenter`, and the kernel returns **`Invalid "netns" value`** (exit 255).

**Fix:** `docker-compose.integration.yml` sets **`pid: "host"`** on:

- **`backend`** — needed if you process workspace jobs via **`POST /internal/workspace-jobs/process`** or any in-process path that runs the orchestrator on the same container as the API.
- **`workspace-worker`** — required for the default integration layout where **`DEVNEST_WORKER_ENABLED=false`** on the API and **`workspace_job_poll_loop`** runs the orchestrator for queued jobs.

**Also required:** bind-mount **`/var/run/docker.sock`** (already present) and **`cap_add: NET_ADMIN`** for bridge/veth operations (already present). Do **not** remove those when adding `pid: host`.

**Verify after deploy** (Linux host):

```bash
docker inspect "$(docker compose -f docker-compose.integration.yml ps -q workspace-worker)" --format '{{.HostConfig.PidMode}}'
# expect: host
```

If the value is empty or not `host`, **recreate** the stack so the compose change applies: `docker compose -f docker-compose.integration.yml up -d --build --force-recreate` (the deploy script does this).

**Workspace vs PID confusion:** Exit **143** on workspace containers after a failed attach is usually **rollback** (`SIGTERM` from `docker stop`), not the IDE exiting before attach. If **`State.Pid`** were wrong because the container had exited, **Docker inspect** would typically show **0** or **not running**; the error shown here is almost always **PID namespace visibility**, not a dead workspace before PID assignment.

**Why previous deploys failed:** (1) `git checkout <sha>` when the shallow or stale clone did not yet have that commit produced **“fatal: reference is not a tree”**; deploys now track **remote branch tips** (`origin/<branch>`). (2) `script_stop` is **not** an input on `appleboy/ssh-action@v1.2.3` and could cause confusion or tool errors; it was removed. (3) **Pull requests** used to look like non-`main` refs; deploy jobs now run only on **`push`** and **`workflow_dispatch`**, never on `pull_request`.

**EC2 instance expectations:** Docker Engine + Compose v2; Git installed; security group allows **22**, **3000**, **8000**; outbound HTTPS for `git fetch` and image pulls.

**Manual redeploy on the instance:**

```bash
export NEXT_PUBLIC_API_BASE_URL="http://<EC2_PUBLIC_IP>:8000"
bash ~/Devnest/scripts/deploy-ec2.sh main          # production
# or
bash ~/Devnest/scripts/deploy-ec2.sh <branch-name> # staging
```

**URLs after deploy** (replace with your `EC2_HOST`):

- UI: `http://<EC2_HOST>:3000`
- API: `http://<EC2_HOST>:8000`
- OpenAPI: `http://<EC2_HOST>:8000/docs`

To trigger tests locally:

```bash
# Integration tests
export DATABASE_URL=postgresql+psycopg://test:test@localhost:5432/devnest_test
cd backend
alembic upgrade head
pytest tests/integration -v
```

---

## Security Hardening

### JWT Secret

```bash
# Generate a cryptographically strong secret
openssl rand -hex 32

# Option A: explicit flag
JWT_SECRET_KEY=<output>
DEVNEST_REQUIRE_SECRETS=true

# Option B: environment-aware (recommended for multi-env deployments)
JWT_SECRET_KEY=<output>
DEVNEST_ENV=production   # or staging; any non-development value triggers enforcement
```

If the default placeholder `change-me-in-production` is used and either `DEVNEST_REQUIRE_SECRETS=true`
or `DEVNEST_ENV` is not `development`, the application raises a `RuntimeError` at startup with a
clear error message.

A `WARNING` is always emitted at startup when the default secret is detected, regardless of
enforcement settings.

### Internal API Keys

```bash
# Generate per-scope keys
for scope in workspace_jobs workspace_reconcile autoscaler infrastructure notifications; do
  echo "${scope}: $(openssl rand -hex 24)"
done
```

Set `DEVNEST_INTERNAL_API_KEY_MIN_LENGTH=24` to enforce key length at startup.

### Database Credentials

- Use a dedicated database user with least-privilege access.
- Never use the postgres superuser in production.
- Enable SSL for the database connection: add `?sslmode=require` to `DATABASE_URL`.

---

## Observability

### Logs

DevNest emits structured logs with a `devnest_event` field. Recommended queries:

```
# Loki: workspace job failures
{job="devnest"} |= "devnest_event" | json | devnest_event = "workspace.job.failed"

# Loki: policy denials
{job="devnest"} |= "devnest_event" | json | devnest_event = "audit.policy.denied"

# Loki: gateway auth denials (session enforcement)
{job="devnest"} |= "devnest_event" | json | devnest_event = "gateway.auth.denied"

# Loki: S3 snapshot upload failures
{job="devnest"} |= "devnest_event" | json | devnest_event = "snapshot.storage.upload.failed"
```

### Metrics

A Prometheus metrics endpoint is available at `/metrics`.

To protect it with an internal API key (recommended in production):

```bash
DEVNEST_METRICS_AUTH_ENABLED=true
INTERNAL_API_KEY_INFRASTRUCTURE=<strong-random-key>
```

When enabled, Prometheus must supply the `X-Internal-API-Key` header with the INFRASTRUCTURE scope key.
If your Prometheus scraper cannot supply headers, protect the endpoint at the ingress layer instead
(e.g. Traefik middleware to restrict to internal IPs) and leave `DEVNEST_METRICS_AUTH_ENABLED=false`.

### Audit Logs

Query the `audit_log` table for a durable record of all security-relevant actions:

```sql
SELECT *
FROM audit_log
WHERE actor_user_id = $1
  AND created_at > NOW() - INTERVAL '7 days'
ORDER BY created_at DESC;
```

---

## Backups and Recovery

### Database

Schedule automated PostgreSQL backups:

```bash
pg_dump -Fc devnest > devnest_$(date +%Y%m%d_%H%M%S).dump
```

Restore:

```bash
pg_restore -d devnest devnest_<timestamp>.dump
```

### Workspace Snapshots

**Local provider** (default / dev):

Snapshots are stored at `DEVNEST_SNAPSHOT_STORAGE_ROOT`. Back up this directory with your standard file backup tooling.

**S3 provider** (production):

```bash
DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3
DEVNEST_S3_SNAPSHOT_BUCKET=your-devnest-snapshots-bucket
DEVNEST_S3_SNAPSHOT_PREFIX=devnest-snapshots
AWS_REGION=us-east-1
```

S3 versioning on the bucket is recommended for durability. The IAM role/instance profile used by the backend needs `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, and `s3:HeadObject` on `arn:aws:s3:::your-bucket/*`.

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:HeadObject"],
    "Resource": "arn:aws:s3:::your-devnest-snapshots-bucket/*"
  }]
}
```

---

## Rollback

To roll back the most recent database migration:

```bash
cd backend
alembic downgrade -1
```

To roll back to a specific revision:

```bash
alembic downgrade 0003
```

**Warning**: Downgrading migrations that include `DROP TABLE` or destructive changes cannot be undone without restoring from a backup. Always back up before running destructive migrations in production.
