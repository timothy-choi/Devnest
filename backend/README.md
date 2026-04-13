# DevNest Backend

FastAPI backend for the DevNest platform — a cloud-hosted coding environment ("Google Drive for coding").

## Requirements

- Python 3.11+
- PostgreSQL 15+ (integration/system tests and production)
- Docker (for workspace orchestration and system tests)

## Quick Start (Local Development)

```bash
cd backend

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env        # or set env vars directly
# Edit .env: set DATABASE_URL, JWT_SECRET_KEY, etc.

# Apply database migrations
alembic upgrade head

# Start the API server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`. Interactive docs at `/docs`.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | *(required)* | PostgreSQL connection string. `postgresql+psycopg://user:pass@host/db` |
| `JWT_SECRET_KEY` | `change-me-in-production` | JWT signing secret. **Must be changed in production.** |
| `DEVNEST_REQUIRE_SECRETS` | `false` | If `true`, startup aborts when `JWT_SECRET_KEY` is the default. Set to `true` in staging/prod. |
| `DEVNEST_WORKER_ENABLED` | `false` | Enable the in-process background job worker. |
| `DEVNEST_WORKER_POLL_INTERVAL_SECONDS` | `5` | Seconds between job poll ticks (1–3600). |
| `DEVNEST_WORKER_BATCH_SIZE` | `5` | Max jobs processed per tick (1–50). |
| `DEVNEST_GATEWAY_ENABLED` | `false` | Enable gateway route registration after container start. |
| `DEVNEST_GATEWAY_URL` | `http://127.0.0.1:9080` | Route-admin URL for the standalone gateway. |
| `INTERNAL_API_KEY` | *(empty)* | Legacy internal auth key. Prefer per-scope keys in production. |

See `app/libs/common/config.py` for the full list of settings with documented defaults.

---

## Database Migrations (Alembic)

DevNest uses [Alembic](https://alembic.sqlalchemy.org/) for schema versioning.

### Applying migrations

```bash
cd backend

# Apply all pending migrations to the database
alembic upgrade head

# Check the current revision
alembic current

# View full migration history
alembic history --verbose
```

### Existing deployments (previously using create_all)

If your database was bootstrapped with `SQLModel.metadata.create_all()` (the previous approach), mark the baseline revision as applied before running new migrations:

```bash
# Mark the initial baseline as applied without re-running it
alembic stamp 0001

# Then apply all subsequent migrations
alembic upgrade head
```

### Creating a new migration

```bash
# Auto-generate a migration from model changes
alembic revision --autogenerate -m "describe the change"

# Review the generated file in alembic/versions/ before applying
alembic upgrade head
```

### Rolling back

```bash
# Roll back the most recent migration
alembic downgrade -1

# Roll back to a specific revision
alembic downgrade 0003
```

### Migration files

| Revision | Description |
|---|---|
| `0001` | Initial baseline: all base tables (users, workspaces, nodes, etc.) |
| `0002` | `workspace_snapshot` table and `workspace_job.workspace_snapshot_id` column |
| `0003` | `audit_log` and `workspace_usage_record` tables |
| `0004` | Change `audit_log.actor_user_id` FK to `ON DELETE SET NULL` |
| `0005` | `policy` and `quota` tables |

---

## Background Job Worker

DevNest processes workspace jobs (container start/stop/delete) asynchronously via a job queue. There are two ways to run the worker:

### Option A: In-process worker (recommended for simple deployments)

Enable the built-in asyncio background worker that runs inside the FastAPI process:

```bash
DEVNEST_WORKER_ENABLED=true
DEVNEST_WORKER_POLL_INTERVAL_SECONDS=5
DEVNEST_WORKER_BATCH_SIZE=5
```

The worker starts automatically when the FastAPI app starts and shuts down gracefully when the process exits.

### Option B: Standalone worker process (recommended for production scale)

Run the standalone poll loop as a separate process (more resilient, independently scalable):

```bash
cd backend
python -m app.workers.workspace_job_poll_loop \
    --poll-interval 2 \
    --jobs-per-tick 5 \
    --log-level INFO
```

### Option C: External HTTP trigger

Call the internal API endpoint to trigger job processing manually or from a cron job / external scheduler:

```bash
curl -X POST http://localhost:8000/internal/workspace-jobs/process \
  -H "X-Internal-API-Key: <key>" \
  -H "Content-Type: application/json"
```

---

## Security: JWT Secret

The `JWT_SECRET_KEY` default value (`change-me-in-production`) is intentionally insecure. DevNest protects against accidental insecure deployments:

1. **Always**: A `WARNING` is logged at startup when the default key is detected.
2. **When `DEVNEST_REQUIRE_SECRETS=true`**: Application startup is aborted with a `ValueError`.

**Generating a strong secret:**

```bash
openssl rand -hex 32
```

Set this value as the `JWT_SECRET_KEY` environment variable. Enable enforcement in staging and production:

```bash
DEVNEST_REQUIRE_SECRETS=true
JWT_SECRET_KEY=<value from openssl rand -hex 32>
```

---

## Running Tests

```bash
cd backend

# Unit tests (no PostgreSQL or Docker required)
pytest tests/unit -v

# Integration tests (requires PostgreSQL at DATABASE_URL)
DATABASE_URL=postgresql+psycopg://test:test@localhost:5432/devnest_test \
  alembic upgrade head
DATABASE_URL=postgresql+psycopg://test:test@localhost:5432/devnest_test \
  pytest tests/integration -v

# System tests (requires PostgreSQL + Docker)
pytest tests/system -v -m "not topology_linux and not topology_linux_core"
```

---

## Project Structure

```
backend/
├── alembic/                  # Alembic migration environment
│   ├── env.py                # Migration environment config
│   ├── script.py.mako        # Template for new revisions
│   └── versions/             # Migration revision files
│       ├── 0001_initial_baseline.py
│       ├── 0002_workspace_snapshot.py
│       ├── 0003_audit_log_and_usage.py
│       ├── 0004_audit_log_fk_set_null.py
│       └── 0005_policy_and_quota.py
├── alembic.ini               # Alembic configuration
├── app/
│   ├── libs/
│   │   ├── common/config.py  # Application settings (pydantic-settings)
│   │   ├── db/database.py    # SQLAlchemy engine, session, init_db
│   │   ├── observability/    # Logging, correlation IDs, metrics
│   │   ├── security/         # Auth dependencies
│   │   └── topology/         # Network topology models
│   ├── services/             # Domain services (workspace, auth, audit, ...)
│   └── workers/
│       ├── lifespan_worker.py          # FastAPI lifespan background worker
│       ├── workspace_job_poll_loop.py  # Standalone worker process
│       └── workspace_job_runner.py     # Job execution entry point
├── migrations/manual/        # Legacy manual SQL scripts (superseded by Alembic)
├── requirements.txt
└── tests/
    ├── unit/                 # Fast tests — no external services
    ├── integration/          # Require PostgreSQL
    └── system/               # Require PostgreSQL + Docker
```
