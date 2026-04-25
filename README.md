# DevNest

DevNest is a **full-stack platform for browser-based developer workspaces**: each user gets an isolated **code-server** container, a **Traefik** edge route, durable **PostgreSQL** metadata, and optional **S3** snapshot archives. It is suitable as a portfolio piece: one compose stack for local smoke tests, the same shape on **EC2 + RDS + S3** for demos.

---

## Project summary

- **Control plane**: FastAPI API, background job worker, gateway route registration, auth (JWT + OAuth), workspace lifecycle orchestration via Docker.
- **Data plane**: Per-workspace Linux containers (code-server) on the Docker host, optional Linux bridge/veth topology for stable addressing, live project files on a host bind mount.
- **Persistence**: RDS (or bundled Postgres) for users, workspaces, jobs, and snapshot metadata; S3 (or local disk) for snapshot **archives**; live edits stay on disk until you save a snapshot.

---

## Architecture overview

At a glance:

1. **Browser** → Next.js UI and Next API routes → FastAPI (`:8000`).
2. **Workspace IDE** → subdomain URL `ws-<id>.<base-domain>` → **Traefik** → code-server in the workspace container.
3. **Worker** dequeues `WorkspaceJob` rows (start/stop/snapshot/reconcile) and drives **Docker** + storage.

For diagrams and component detail, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). For compose env and EC2/RDS checks, see [docs/INTEGRATION_STARTUP.md](docs/INTEGRATION_STARTUP.md).

---

## Features

| Area | What you get |
|------|----------------|
| Workspaces | Create, start, stop, open in browser (code-server), optional terminal (feature-gated). |
| Auth | Email/password and GitHub / Google OAuth (when configured). |
| Gateway | Dynamic Traefik routes + optional ForwardAuth session check. |
| Snapshots | Save workspace → queued export + archive; download `.tar.gz`; restore path documented in UI/docs. |
| Jobs | Mutations go through `WorkspaceJob` with retries and stuck-job reclaim. |
| Resilience | Bring-up rollback, reconcile loop, topology janitor, cleanup tasks (see architecture doc). |

---

## Tech stack

| Layer | Technologies |
|-------|----------------|
| UI | Next.js (React), TypeScript |
| API | Python 3.11+, FastAPI, SQLModel, Alembic |
| DB | PostgreSQL 15+ |
| Workspaces | Docker, code-server (custom image `Dockerfile.workspace`) |
| Edge | Traefik, in-repo route-admin sidecar |
| Snapshots (cloud) | AWS S3 (boto3), IAM or keys via standard AWS env |
| CI / ops | GitHub Actions, `docker compose` integration file |

---

## Quick start (local integration stack)

**Prerequisites:** Docker with Compose v2, enough disk/RAM for Postgres + backend + worker + frontend + Traefik + occasional workspace containers.

1. **Env file** (repo root):

   ```bash
   cp .env.integration.example .env.integration
   ```

   Defaults target **bundled Postgres** and `app.lvh.me` for workspace subdomains when the browser runs on the same machine as Docker. Adjust [`.env.integration.example`](.env.integration.example) only if you need remote browsers or RDS (see comments inside the file).

2. **Start everything** (validates env, runs compose, health checks):

   ```bash
   ./scripts/deploy_integration.sh
   ```

   Override the file: `ENV_FILE=.env.custom ./scripts/deploy_integration.sh`.

3. **Open the app:** [http://localhost:3000](http://localhost:3000) (or your `DEVNEST_FRONTEND_PUBLIC_BASE_URL`).

4. **API health:** [http://localhost:8000/ready](http://localhost:8000/ready).

Backend-only or frontend-only development may use `backend/` and `frontend/` READMEs; the integration compose file is the **recommended** path for an end-to-end demo on one machine.

---

## EC2 / RDS / S3 deployment summary

- **EC2** runs Docker Compose from [`docker-compose.integration.yml`](docker-compose.integration.yml): API, worker, Traefik, route-admin, frontend, and (unless using RDS) bundled Postgres.
- **RDS** holds application data. Set a **single-line** SQLAlchemy URL, e.g. `postgresql+psycopg://USER:PASSWORD@host.region.rds.amazonaws.com:5432/dbname?sslmode=require`, via `DATABASE_URL` or `DEVNEST_COMPOSE_DATABASE_URL` (compose copies into `DEVNEST_DATABASE_URL` for the app).
- **S3** stores snapshot archives when `DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3`. With external Postgres or `DEVNEST_EXPECT_*` fail-fast flags, **S3 is required**—the deploy script validates this.
- **Public URLs:** Set `DEVNEST_FRONTEND_PUBLIC_BASE_URL`, `NEXT_PUBLIC_API_BASE_URL`, and `DEVNEST_BASE_DOMAIN` so OAuth redirects and workspace subdomains resolve for **remote** browsers (not `app.lvh.me` for clients off the host). See [`scripts/deploy-ec2.sh`](scripts/deploy-ec2.sh) and [docs/INTEGRATION_STARTUP.md](docs/INTEGRATION_STARTUP.md).

---

## Demo flow

A step-by-step script (including optional S3 verification) lives in [docs/DEMO.md](docs/DEMO.md). In short: register → create workspace → open IDE → edit a file → **Save workspace** on the dashboard → **Download workspace** → (optional) confirm object in S3 → restart stack → reopen workspace.

---

## Known limitations

- **Single-region, Docker-first:** No Kubernetes execution path in tree; topology is built for Linux bridge/veth on the node.
- **Gateway DNS:** You must provide a base domain whose wildcard (`*.domain`) points at Traefik for remote users; automation for Route53 is not in-repo.
- **Rate limits / SSE:** Default rate limiting is in-memory per process; SSE push is best in single-process; multi-process falls back to polling (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)).
- **Secrets:** Replace integration defaults (`JWT_SECRET_KEY`, internal API keys) before any real deployment; use `DEVNEST_ENV` / `DEVNEST_REQUIRE_SECRETS` for enforcement (see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).

---

## Documentation index

| Doc | Purpose |
|-----|---------|
| [docs/DEMO.md](docs/DEMO.md) | Exact demo checklist |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Control plane, runtime, Traefik, storage, failure handling |
| [docs/INTEGRATION_STARTUP.md](docs/INTEGRATION_STARTUP.md) | Compose, RDS, OAuth, gateway ports |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Production-style checklist |
| [docs/WORKSPACE_PERSISTENCE.md](docs/WORKSPACE_PERSISTENCE.md) | Disk layout and code-server mounts |
| [docs/CODE_SERVER.md](docs/CODE_SERVER.md) | IDE container behavior |

---

## Repository layout (high level)

- `backend/` — FastAPI app, workers, Alembic migrations  
- `frontend/` — Next.js UI  
- `devnest-gateway/` — Route registration helper consumed by Traefik  
- `scripts/deploy_integration.sh` — Local/CI one-command bring-up  
- `scripts/deploy-ec2.sh` — EC2-oriented deploy helper  
- `docker-compose.integration.yml` — Full stack definition
