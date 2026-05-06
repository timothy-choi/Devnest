# DevNest reliability and chaos drills

This document describes **realistic infrastructure failures**, what DevNest is expected to do afterward, how to **run chaos helpers**, and what **logs/metrics** prove recovery. Pair it with [`scripts/chaos/README.md`](scripts/chaos/README.md).

**Platform vocabulary**

- Workspace **CREATING** is the control-plane state while bring-up jobs run (maps to “provisioning” in ops language).
- Recovery usually involves **queued workspace jobs**, optional **`RECONCILE_RUNTIME`**, **autoscaler** (EC2 fleet), **cleanup/durable cleanup tasks**, and **EC2 orphan janitor** when enabled.

---

## Observability cheat sheet

Capture logs from **API**, **workspace-worker**, **autoscaler loop** (if enabled), and **PostgreSQL** job/workspace rows during drills.

| Signal | Where | Meaning |
|--------|--------|---------|
| `workspace.job.retry_scheduled` | Structured `LogEvent` / JSON logs | Job scheduled for retry after backoff |
| `workspace_job_retry_scheduled` | `extra=` style worker logs | Same retry, alternate logger key |
| `workspace.retry.attempt` | Worker (`workspace.retry.attempt`) | Retry attempt diagnostics (e.g. `node_readiness`) |
| `workspace.job.failed` / `workspace.job.failed_terminal` | Structured events | Terminal vs potentially retryable failure |
| `workspace.job.retry_exhausted` | Structured events | No attempts remaining |
| `reconcile.started` / `workspace.recovery.reconcile` | Reconcile loop / recovery paths | Reconcile engagement |
| `autoscaler.scale_up.reason` | Autoscaler service | Scale-out decision context |
| `autoscaler.scale_down.*` | Autoscaler service | Scale-in guards / skips |
| `cleanup_task_*` / `devnest_cleanup_task_*` | Cleanup worker + metrics | Durable cleanup debt processing |
| `ec2_orphan_janitor_*` | FastAPI janitor loop | Orphan EC2 reconciliation (when enabled) |

**Suggested grep / ripgrep**

```bash
rg 'workspace\.job\.(retry_scheduled|failed|retry_exhausted)|workspace_job_retry_scheduled|workspace\.retry\.attempt|reconcile\.|autoscaler\.scale_|cleanup_task_|ec2_orphan_janitor_' logs/
```

---

## Scenario 1 — Kill workspace-worker during workspace CREATING

**Failure**

The job worker (`workspace-worker` in compose, or `workspace_job_poll_loop`) dies mid-tick while a **CREATE**/**START** job is **RUNNING** or workspace is **CREATING** (e.g. SIGKILL during orchestrator bring-up).

**Expected behavior**

- In-flight job may stall in **RUNNING** until **timeout/reconcile** or manual intervention (exact behavior depends on `workspace_job_max_attempts`, heartbeat of worker, and whether another runner claims the row).
- After worker restart, **`FOR UPDATE SKIP LOCKED`** dequeue continues; jobs may be **retried** (`workspace.job.retry_scheduled`) if attempts remain and failure is classified retryable.
- Operator can **`POST /internal/workspace-jobs/process`** to drain queue deterministically.

**Commands**

```bash
# From repo root (integration stack)
ENV_FILE=/tmp/devnest-integration.env ./scripts/chaos/kill_workspace_worker.sh --restart

# Or kill without restart for a longer stall experiment:
./scripts/chaos/kill_workspace_worker.sh

# Drain jobs (replace URL + scoped internal API key)
curl -sS -X POST "http://localhost:8000/internal/workspace-jobs/process?limit=10" \
  -H "X-Internal-API-Key: ${INTERNAL_API_KEY_WORKSPACE_JOBS}"
```

**Verify**

- DB: `workspace_job` rows (`status`, `attempt`, `next_attempt_after`), `workspace.status`.
- Logs: `workspace.job.retry_scheduled`, `workspace.retry.attempt`, eventual `workspace.job.succeeded` or `workspace.job.failed_terminal`.

**Observed recovery** *(fill during drill)*

| Step | Timestamp | Notes |
|------|-----------|-------|
| Kill worker | | |
| Workspace/job state | | |
| Worker up | | |
| POST /process | | |
| Terminal status | | |

**Known limitations**

- A row stuck **RUNNING** without a live worker may require **reconcile**, increasing **`attempt`**, or DB operator reset depending on deployment policy (document local runbook).

---

## Scenario 2 — Restart Docker daemon on an execution node (workspace RUNNING)

**Failure**

`systemctl restart docker` (or equivalent) on the host that runs **user workspace containers**. All containers on that host stop; runtime state diverges from DB **RUNNING**.

**Expected behavior**

- TCP/HTTP probes and orchestrator inspect paths fail for affected workspaces.
- **`RECONCILE_RUNTIME`** jobs (periodic loop `devnest_reconcile_enabled` or manual enqueue) compare desired vs observed state and may stop/recreate containers or mark workspace **ERROR** when inconsistent.
- **Durable cleanup tasks** may record stop/bring-up rollback debt (`cleanup_task_*` logs / metrics).

**Commands**

```bash
SSH_TARGET=ec2-user@10.0.1.50 EXECUTION_HOST=10.0.1.50 ./scripts/chaos/restart_docker_execution_host.sh

# Optional dry run
DRY_RUN=1 SSH_TARGET=ec2-user@10.0.1.50 ./scripts/chaos/restart_docker_execution_host.sh
```

**Verify**

- Logs: `orchestrator.bringup.failed`, `workspace.job.retry_scheduled`, `reconcile.started`, `cleanup_task_*`.
- DB: `workspace_runtime`, topology attachment rows, `workspace_cleanup_task`.

**Observed recovery**

| Step | Timestamp | Notes |
|------|-----------|-------|
| Docker restart | | |
| Workspace reachability | | |
| Reconcile fired | | |
| Final workspace status | | |

**Known limitations**

- Linux topology attach (`ip link set … netns`) needs a healthy Docker + worker netns on that host; split-brain cases may need **manual** stop/start.

---

## Scenario 3 — Terminate EC2 execution node unexpectedly

**Failure**

EC2 instance powering an execution node stops with **no graceful drain** (`terminate-instances`).

**Expected behavior**

- Heartbeats stop; placement may mark node **NOT_READY** / **ERROR** / lifecycle cleanup paths via infra/autoscaler depending on configuration.
- Workloads pinned to that node fail orchestration until **rescheduled** on another node (multi-node placement + autoscaler) or marked **ERROR**.
- **`autoscaler.scale_up.reason`** may appear if fleet demand exceeds ready capacity and **`devnest_autoscaler_provision_on_no_capacity`** / loop policies allow provisioning.
- **`ec2_orphan_janitor_*`** logs when `DEVNEST_EC2_ORPHAN_JANITOR_ENABLED` reconciles stray instances.

**Commands**

```bash
CONFIRM=yes INSTANCE_ID=i-0123456789abcdef0 AWS_REGION=us-east-1 ./scripts/chaos/terminate_ec2_execution_node.sh
```

**Verify**

- AWS: instance state → `terminated`.
- DB: `execution_node` status transitions; workspace placements.
- Logs: autoscaler capacity logs, orphan janitor, workspace job failures/retries.

**Observed recovery**

| Step | Timestamp | Notes |
|------|-----------|-------|
| Terminate | | |
| Heartbeat loss | | |
| Replacement capacity | | |
| Workspace outcomes | | |

**Known limitations**

- Single-node dev stacks have **no** other node to absorb load — expect **ERROR** or manual restore.
- EC2 provisioning latency + AMI/bootstrap means recovery may exceed workspace capacity retry windows — tune **`workspace_capacity_retry_timeout_seconds`**.

---

## Scenario 4 — Simulate SSM command failure

**Failure**

SSM `SendCommand` fails (IAM deny, VPC egress block, SSM agent down).

**Expected behavior**

- Bring-up/stop/update paths using **`SsmDockerRuntimeAdapter`** surface **`WorkspaceBringUpError`** / job failures.
- **`workspace.job.retry_scheduled`** when retries remain.
- No automatic “fix” without restoring SSM path.

**Commands**

See runbook output:

```bash
./scripts/chaos/simulate_ssm_failure.sh
```

Automated references: unit tests mock SSM runner (e.g. `backend/tests/unit/node_execution_service/test_orchestrator_ssm_wiring.py`).

**Verify**

- Logs containing `SendCommand`, `SSM`, or orchestrator binding errors.
- Job `failure_code` / `failure_stage` columns.

**Known limitations**

- Full end-to-end SSM chaos needs **staging AWS**; local compose typically uses **Docker socket**, not SSM.

---

## Scenario 5 — Simulate S3 snapshot failure

**Failure**

Snapshot export/import cannot complete (`PutObject` denied, wrong bucket, expired credentials).

**Expected behavior**

- **`workspace.snapshot.failed`** / orchestrator snapshot logs.
- Snapshot job may **retry** then **`workspace.job.failed_terminal`** when exhausted.
- Workspace data on bind mounts untouched; **archive** step fails.

**Commands**

```bash
./scripts/chaos/simulate_s3_snapshot_failure.sh
```

**Verify**

- Logs: `orchestrator.snapshot.export_started`, `workspace.snapshot.failed`.
- DB: snapshot rows + workspace job outcomes.

**Known limitations**

- Local provider **`local`** skips real S3 — use **`devnest_snapshot_storage_provider=s3`** in staging.

---

## Automated guardrails

- **`backend/tests/chaos/`** — lightweight tests that `RELIABILITY.md` and chaos scripts stay present (does **not** execute destructive drills in CI).
- **Merge CI:** `.github/workflows/tests.yml` **Unit Tests** job runs `pytest tests/chaos -m chaos` after `tests/unit` (see workflow).
- **Nightly:** `pytest tests` in `nightly.yml` already collects `tests/chaos`.
- Run locally: `pytest backend/tests/chaos -m chaos`

---

## Recording results

For each production-like drill, archive:

1. Redacted log excerpt with **timestamps** covering kill → recovery window.
2. Before/after **`workspace`**, **`workspace_job`**, **`workspace_runtime`**, **`execution_node`** snapshots (SQL `\copy` or admin export).
3. Link to PR/issue describing configuration (**autoscaler**, **reconcile**, **retry** limits).
