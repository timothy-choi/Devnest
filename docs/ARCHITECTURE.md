# DevNest Architecture

## Overview

DevNest is a cloud-hosted coding environment platform — a "Google Drive for coding." It provisions isolated workspace containers per user, manages their lifecycle, and exposes them through a reverse proxy gateway.

---

## High-Level Architecture

```
                        ┌───────────────────────────────────────────────┐
                        │                  Client                        │
                        └───────────────────┬───────────────────────────┘
                                            │ HTTPS
                        ┌───────────────────▼───────────────────────────┐
                        │            Traefik (Gateway)                   │
                        │   Dynamic routes via devnest-gateway sidecar   │
                        └───────────────────┬───────────────────────────┘
                                            │
                  ┌─────────────────────────▼──────────────────────────┐
                  │                  DevNest API (FastAPI)              │
                  │                                                      │
                  │  ┌────────────┐  ┌──────────────┐  ┌────────────┐  │
                  │  │ Workspace  │  │  Auth / User  │  │ Audit/     │  │
                  │  │ Service    │  │  Service      │  │ Usage      │  │
                  │  └──────┬─────┘  └───────────────┘  └────────────┘  │
                  │         │                                             │
                  │  ┌──────▼──────────────────────────────────────────┐ │
                  │  │              Job Queue (WorkspaceJob table)      │ │
                  │  └──────┬──────────────────────────────────────────┘ │
                  │         │                                             │
                  │  ┌──────▼──────────────────────────────────────────┐ │
                  │  │  Worker (lifespan_worker / standalone poller)   │ │
                  │  └──────┬──────────────────────────────────────────┘ │
                  └─────────┼───────────────────────────────────────────┘
                             │
                  ┌──────────▼──────────────────────────────────────────┐
                  │         Orchestrator Service (Docker)                 │
                  │    Container lifecycle: start / stop / delete         │
                  └──────────┬──────────────────────────────────────────┘
                              │
                  ┌───────────▼─────────────────────────────────────────┐
                  │            Execution Nodes (local / EC2)              │
                  │         Docker containers per workspace               │
                  └─────────────────────────────────────────────────────┘
```

---

## Component Responsibilities

### API Layer (`app/main.py`)

- FastAPI application with a lifespan context managing startup and shutdown.
- Registers all service routers and global exception handlers.
- Optionally starts the in-process background job worker (`lifespan_worker`).

### Workspace Service

- **Intent API** (`/workspaces`): Creates, starts, stops, and deletes workspaces.
- **Jobs**: All mutations enqueue `WorkspaceJob` rows; execution is decoupled.
- **Sessions**: Issues short-lived session tokens for workspace access.
- **Snapshots**: Creates and restores point-in-time workspace archives.

### Orchestrator Service

- Interfaces with Docker to manage workspace container lifecycle.
- Abstracts over local Docker and EC2-hosted Docker nodes.
- Returns structured `OrchestratorResult` objects for worker processing.

### Worker Layer

Three execution modes (can coexist safely):

| Mode | Module | Use Case |
|---|---|---|
| In-process async | `app/workers/lifespan_worker.py` | Simple single-process deployments |
| Standalone process | `app/workers/workspace_job_poll_loop.py` | Production; separate scaling |
| HTTP trigger | `POST /internal/workspace-jobs/process` | Cron, CI, manual ops |

All modes share the same dequeue semantics (`FOR UPDATE SKIP LOCKED`; per-job commit).

**Stuck-job reclaim**: The in-process worker detects jobs that have been in `RUNNING`
state longer than `WORKSPACE_JOB_STUCK_TIMEOUT_SECONDS` (default 300s) and either
retries them (if attempts remain) or marks them `FAILED` terminal. Lifecycle jobs
(`START`, `STOP`, etc.) also move the workspace to `ERROR` on terminal reclaim.
Reconcile and snapshot jobs do not move the workspace to `ERROR`.

### Automated Reconcile Loop

`app/workers/lifespan_reconcile.py` runs as a FastAPI lifespan background task
(when `DEVNEST_RECONCILE_ENABLED=true`). On each tick it queries workspaces in the
target statuses (default: `RUNNING,ERROR`) and calls `enqueue_reconcile_runtime_job`
for each. The loop is idempotent — the **reconcile lease** mechanism ensures no
duplicate jobs are enqueued:

- **QUEUED** reconcile job exists → `WorkspaceBusyError` raised; loop silently skips.
- **RUNNING** reconcile job within `DEVNEST_RECONCILE_LEASE_TTL_SECONDS` → skipped.
- **RUNNING** reconcile job older than TTL → stale (crashed worker); allow re-enqueue.

Configuration:

| Variable | Default | Description |
|---|---|---|
| `DEVNEST_RECONCILE_ENABLED` | `false` | Enable background loop |
| `DEVNEST_RECONCILE_INTERVAL_SECONDS` | `30` | Tick cadence (floor 10s) |
| `DEVNEST_RECONCILE_BATCH_SIZE` | `10` | Max workspaces per tick |
| `DEVNEST_RECONCILE_TARGET_STATUSES` | `RUNNING,ERROR` | Comma-separated statuses |
| `DEVNEST_RECONCILE_LEASE_TTL_SECONDS` | `120` | Stale-running threshold |

### SSE / Event Delivery

Events are persisted as append-only `WorkspaceEvent` rows and streamed via
Server-Sent Events on `GET /workspaces/{id}/events`.

**Push-notification bus** (`app/libs/events/workspace_event_bus.py`): When
`record_workspace_event` commits a row it calls `notify_workspace_event(workspace_id)`,
which signals an `asyncio.Event` for every SSE generator subscribed to that workspace.
Generators wake immediately instead of waiting for the next poll interval.

- Single-process: push notification fires within milliseconds of the commit.
- Multi-process: generators in other processes fall back to periodic polling
  (every `EVENT_BUS_WAIT_TIMEOUT_SEC` = 2s).

**Resume cursor**: SSE accepts `?last_event_id=N` to replay only events after a known
cursor, avoiding full-history replay on reconnect.

### Scheduler and Placement

- `placement_service`: SQL-level node selection (capacity-first, spread-aware).
- `scheduler_service`: Python-side ranking with fairness guardrails.
- `autoscaler_service`: Conservative scale-up/down with cost-aware suppression.

### Policy and Quota

- `policy_service`: Evaluates named policies before workspace mutations.
- `quota_service`: Enforces numeric limits (workspace count, CPU, storage, etc.).
- Violations raise `PolicyViolationError` (HTTP 403) or `QuotaExceededError` (HTTP 429).

### Audit and Usage

- `audit_service`: Append-only audit log; records who did what, when, and why.
- `usage_service`: Event-based usage records for quota enforcement and billing.

### Gateway Integration

- `gateway_client`: Registers/deregisters per-workspace routes with the Traefik sidecar.
- Routes use subdomain format: `{workspace-id}.{DEVNEST_BASE_DOMAIN}`.
- **ForwardAuth** (`GET /internal/gateway/auth`): Traefik calls this endpoint before proxying workspace traffic. The backend validates the workspace session token (`X-DevNest-Workspace-Session`), confirms the session is ACTIVE and unexpired, and confirms the workspace is RUNNING. Returns 200 to allow or 401 to deny.
- **TLS**: Traefik's `websecure` entrypoint is configured on `:443`. Local/dev uses Traefik's built-in self-signed certificate. Production uses Let's Encrypt ACME (configured in `traefik.yml` via `certificatesResolvers`).

### Snapshot Storage

- **Interface**: `SnapshotStorageProvider` protocol (`app/services/storage/interfaces.py`).
- **Local provider** (`LocalFilesystemSnapshotStorage`): default; stores archives under `{root}/ws-{id}/snapshot-{snap_id}.tar.gz`. Suitable for single-node / dev.
- **S3 provider** (`S3SnapshotStorageProvider`): stores archives in S3 under `s3://{bucket}/{prefix}/ws-{id}/snapshot-{id}.tar.gz`. Archives are staged locally before upload / after download. Worker calls `upload_archive()` after export and `download_archive()` before restore.
- Provider is selected via `DEVNEST_SNAPSHOT_STORAGE_PROVIDER=local|s3`. Credentials use the boto3 credential chain.

### Networking (`libs/topology`)

- Models network topologies (bridges, veth pairs, IP allocations).
- V1: managed locally; production networking deferred to managed VPC/EKS.

---

## Database

- **ORM**: SQLModel (SQLAlchemy + Pydantic).
- **Migrations**: Alembic (`backend/alembic/`). See `backend/README.md`.
- **Session management**: Per-request FastAPI dependency (`get_db`); per-job sessions in the worker.

---

### Rate Limiting

`app/libs/security/rate_limit.py` implements a thread-safe in-memory sliding-window
rate limiter with no external dependencies:

| Layer | Scope | Default |
|---|---|---|
| `RateLimitMiddleware` (global) | per-IP, all routes | 300 req/min |
| `auth_rate_limit` dependency | per-IP, auth endpoints | 20 req/min |
| `sse_rate_limit` dependency | per-IP, SSE endpoint | 30 req/min |

- Disable globally: `DEVNEST_RATE_LIMIT_ENABLED=false`.
- Blocked requests receive HTTP 429 with a `Retry-After` header.
- Multi-process note: each process has its own window; for production with many workers,
  replace with a Redis-backed limiter.

---

## Security Model

- **User auth**: JWT access tokens + opaque refresh tokens. Passwords hashed with bcrypt.
- **Internal auth**: `X-Internal-API-Key` header with per-scope keys; validated by `InternalApiScope`.
- **JWT secret enforcement**: Warning on default key; startup abort when `DEVNEST_REQUIRE_SECRETS=true`.
- **Workspace sessions**: HMAC-SHA256 session tokens; short-lived with TTL.
- **Gateway ForwardAuth**: Workspace data-plane traffic is protected by session validation at the Traefik edge. Only users with a valid, non-expired ACTIVE session for a RUNNING workspace are allowed through. Enable in production with `DEVNEST_GATEWAY_AUTH_ENABLED=true` on both the backend and route-admin.

---

## Observability

- **Structured logging**: `log_event()` with `devnest_event` field for Loki/CloudWatch queries.
- **Correlation IDs**: `CorrelationIdMiddleware` injects a UUID per request; propagated through async worker ticks.
- **Audit logs**: Durable per-action records in `audit_log` table.
- **Metrics**: `prometheus-client` endpoint at `/metrics`.

---

## Known Gaps and Deferred Items

| Area | Status | Notes |
|---|---|---|
| Full RBAC | Deferred | Policy/Quota V1 is a foundation; ABAC/RBAC planned |
| Billing engine | Deferred | Usage events are the foundation; invoicing not implemented |
| Kubernetes/EKS | Deferred | Docker-based orchestration only in V1 |
| Self-managed networking | Partial | Topology models exist; production should use managed VPC |
| GitHub/AI/CI integration | Deferred | Planned product features |
| Monitoring/alerting | Partial | Prometheus metrics endpoint; no alerting rules yet |
| Multi-region | Deferred | Single-region only in V1 |
| Gateway auth (ForwardAuth) | Implemented | `GET /internal/gateway/auth`; enable with `DEVNEST_GATEWAY_AUTH_ENABLED=true` |
| TLS / HTTPS | Implemented | Traefik `websecure` entrypoint; local self-signed; production ACME configured in `traefik.yml` |
| S3 snapshot storage | Implemented | `S3SnapshotStorageProvider`; select with `DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3` |
| Route53 DNS automation | Deferred | Manual DNS setup required for production domains |
| Advanced cert rotation | Deferred | ACME handles renewal; multi-domain cert management deferred |
