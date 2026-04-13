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

### Networking (`libs/topology`)

- Models network topologies (bridges, veth pairs, IP allocations).
- V1: managed locally; production networking deferred to managed VPC/EKS.

---

## Database

- **ORM**: SQLModel (SQLAlchemy + Pydantic).
- **Migrations**: Alembic (`backend/alembic/`). See `backend/README.md`.
- **Session management**: Per-request FastAPI dependency (`get_db`); per-job sessions in the worker.

---

## Security Model

- **User auth**: JWT access tokens + opaque refresh tokens. Passwords hashed with bcrypt.
- **Internal auth**: `X-Internal-API-Key` header with per-scope keys; validated by `InternalApiScope`.
- **JWT secret enforcement**: Warning on default key; startup abort when `DEVNEST_REQUIRE_SECRETS=true`.
- **Workspace sessions**: HMAC-SHA256 session tokens; short-lived with TTL.

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
