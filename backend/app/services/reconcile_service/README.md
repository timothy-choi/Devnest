# Reconcile service (V1)

Conservative **RECONCILE_RUNTIME** jobs compare **desired** state (`Workspace.status` and persisted `WorkspaceRuntime`) to **actual** state (orchestrator `check_workspace_runtime_health`, optional route-admin `GET /routes`). The control plane remains authoritative; repairs are limited to safe, idempotent actions.

This matches the usual loop: **observe → compare → repair or fail → emit events**.

## Alignment with project documentation (Reconcile Runtime diagram)

The **DevNest Project Documentation** PDF (Reconcile Runtime section) describes a **target-state** control loop. V1 implements a **deliberate subset** so behavior stays safe and idempotent without duplicating full orchestration inside reconcile.

| Diagram step | Documentation intent | V1 implementation |
|--------------|----------------------|-------------------|
| **Trigger sources** | Scheduler tick, gateway 502/WS failures, runtime events, manual `POST /internal/workspaces/{id}/reconcile` | **Manual only:** `POST /internal/workspaces/{id}/reconcile-runtime` + worker process. Scheduler / gateway-driven enqueue **deferred**. |
| **Enqueue** | `ReconcileRuntime(workspace_id, reason)` | `WorkspaceJob` type `RECONCILE_RUNTIME`; **no `reason` payload** on the job row yet (optional follow-up). |
| **Lock** | Acquire reconcile lock; exit if held | **Not implemented.** Same job worker + row locks as other jobs; no per-workspace reconcile lease. **Deferred** per “no distributed locking unless necessary.” |
| **Load state** | Workspace row, `stop_requested`, sessions | Loads `Workspace` + `WorkspaceJob` context; **does not** read `stop_requested` (not in current schema). Uses orchestrator **read-only** health to load **actual** runtime. |
| **Desired outcome** | Derive STOP vs RUNNING vs DELETE from status + flags | **Direct mapping** from settled `Workspace.status`: `RUNNING` / `STOPPED` / `ERROR` / `DELETED`. Transactional `*ING` statuses **reject enqueue** (busy)—reconcile does not replace normal intents. |
| **Branch A — EnsureStopped** | Stop container, unregister route, tear topology, volumes | **Partial:** `stop_workspace_runtime` (orchestrator owns topology detach) + **strict / best-effort** gateway deregister. No separate volume teardown in reconcile. |
| **Branch A — DB after stop** | `STOPPED`, `endpoint_ref` NULL, `runtime.stopped` SSE | Worker **finalize** matches existing STOP job semantics (`STOPPED`, runtime row updated). Events are **`controlplane.*`** (e.g. `reconcile_fixed_runtime`, `reconcile_cleaned_orphan`), not the diagram’s `runtime.stopped` name—same stream table, different type strings. |
| **Branch B — Pre-flight** | `FAILED` → exit unless restart/policy; `RECOVERING` | **V1:** If control plane says `RUNNING` but health fails → **job failed**, workspace **`ERROR`** (canonical app state), `reconcile_failed`. No `RECOVERING` status or auto-heal policy. |
| **Discover actual** | Container, storage, topology | **Container + probe** via `check_workspace_runtime_health` only. **No** reconcile-time storage or topology inspection beyond what orchestrator health already reflects. |
| **Ensure prereqs / container** | Create volume, topology, start/create container | **Explicitly out of scope for V1** (no surprise bring-up). Drift that needs provisioning → **ERROR** + operator/user runs normal **start/create** intents. |
| **Ensure gateway route** | `RegisterRoute` | **Implemented** for RUNNING when route missing/wrong vs observed internal endpoint. |
| **Verify E2E** | Gateway→container, WS, IDE | **Not** a separate reconcile phase; covered by existing **probe** inside health check where configured. |
| **Success** | `RUNNING`, clear errors, `runtime.running` | Stays **`RUNNING`**; sync `WorkspaceRuntime` + optional gateway register; events **`reconcile_*`** + standard **`job_succeeded`**. |
| **Failure path** | Stages, retries, cleanup policy, `runtime.failed` | Single conservative path: **`reconcile_failed`**, job failed; **RUNNING** drift → workspace **ERROR**. No multi-stage retry/backoff inside reconcile. |

**Summary:** The diagram is the **north star** for a full reconcile engine. The current codebase implements the **diagnostics + gateway alignment + safe stop** slices of **Branch A** and the **observe + gateway repair + failure classification** slices of **Branch B**, while **deferring** locks, schedulers, auto-provisioning, `reason` metadata, and `runtime.*` event naming parity.

## Allowed enqueue statuses

`RUNNING`, `STOPPED`, `ERROR`, `DELETED` — not while `CREATING` / `STARTING` / `*ING` busy.

## Repairs

| Status   | Drift                         | Action                                      |
|----------|-------------------------------|---------------------------------------------|
| RUNNING  | Health not successful         | Job failed; workspace → `ERROR`             |
| RUNNING  | Healthy; DB snapshot stale    | Sync `WorkspaceRuntime` from health         |
| RUNNING  | Healthy; gateway route wrong/missing | Re-register route (`DEVNEST_GATEWAY_ENABLED`) |
| STOPPED  | Container still running       | `stop_workspace_runtime` (same finalize as STOP job); then **best-effort** strict orphan route DELETE if route-admin still lists a row |
| STOPPED  | Orphan gateway route only   | Deregister (strict)                         |
| ERROR    | Orphan gateway route          | Deregister (strict)                         |
| DELETED  | Orphan gateway route          | Deregister (strict)                         |

## SSE / persisted event types (`controlplane.*`)

| Event | Meaning |
|-------|---------|
| `reconcile_started` | Reconcile job body entered |
| `reconcile_fixed_runtime` | Runtime row synced from health, or lingering container stopped under STOPPED |
| `reconcile_fixed_route` | Gateway route (re)registered for RUNNING repair |
| `reconcile_cleaned_orphan` | Gateway route removed when workspace should not advertise (deleted / error / stopped orphan, or post-stop strict cleanup) |
| `reconcile_noop` | No repair needed |
| `reconcile_failed` | Reconcile could not complete safely (includes stop finalize failure on STOPPED branch) |

## Idempotency

- Repeated RUNNING reconciles with unchanged reality yield `reconcile_noop` (no duplicate gateway POST unless target drifted).
- Orphan removal and deregister are idempotent with route-admin.
- After a successful stop reconcile, worker best-effort deregister may fail; a **second** strict cleanup attempt runs in-process and logs on failure without failing an already-succeeded job.

## Deferred

Auto bring-up for missing RUNNING runtime, cron/leader reconciliation, EC2/Kubernetes, distributed locks, TLS/DNS, aggressive multi-step healing.

## Trigger

`POST /internal/workspaces/{workspace_id}/reconcile-runtime` (internal API key), then process queue via existing `/internal/workspace-jobs/process`.
