# DevNest Deployment Guide

## Prerequisites

| Requirement | Minimum Version | Notes |
|---|---|---|
| Python | 3.11 | Required for the FastAPI backend |
| PostgreSQL | 15 | Production database |
| Docker | 24.0 | Workspace container orchestration |
| Alembic | 1.13+ | Schema migrations (included in `requirements.txt`) |

---

## Production Deployment Checklist

### 1. Environment Variables

Set the following before starting the application:

```bash
# Required
DATABASE_URL=postgresql+psycopg://devnest:STRONG_PASSWORD@db-host:5432/devnest
JWT_SECRET_KEY=$(openssl rand -hex 32)

# Production enforcement (prevents accidental insecure deployments)
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

# Gateway (optional; enable after Traefik sidecar is running)
DEVNEST_GATEWAY_ENABLED=true
DEVNEST_GATEWAY_URL=http://route-admin:9080
DEVNEST_BASE_DOMAIN=app.yourdomain.com
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

### 4. Worker Configuration

Choose the job worker mode that fits your deployment:

#### Option A: In-process worker (simple single-process deployments)

```bash
DEVNEST_WORKER_ENABLED=true
```

The worker starts automatically with the FastAPI process and shuts down gracefully with it.

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

The CI pipeline (`tests.yml`) automatically:

1. Spins up PostgreSQL as a service container.
2. Installs Python dependencies.
3. Runs `alembic upgrade head` against the test database.
4. Runs unit, integration, and system test suites.

To trigger locally:

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

# Set it and enforce it
JWT_SECRET_KEY=<output>
DEVNEST_REQUIRE_SECRETS=true
```

If `DEVNEST_REQUIRE_SECRETS=true` and the default placeholder `change-me-in-production` is used,
the application will refuse to start with a clear error message.

Even without `DEVNEST_REQUIRE_SECRETS=true`, a `WARNING` is always emitted at startup when the
default secret is detected.

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
```

### Metrics

A Prometheus metrics endpoint is available at `/metrics`.

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

Workspace snapshots are stored at `DEVNEST_SNAPSHOT_STORAGE_ROOT`. Back up this directory with your standard object storage or file backup tooling.

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
