# DevNest Operations Guide

This document covers day-to-day operations: health checking, runbooks, scaling, maintenance
procedures, and monitoring guidance.

---

## Reconcile, topology janitor, and metrics

- **Advisory lock contention:** `devnest_reconcile_lock_contended_total` increments when a worker skips
  a reconcile because another holds the per-workspace PostgreSQL advisory lock; jobs retry with
  backoff (`reconcile:advisory_lock_contended`).
- **Janitor:** `devnest_topology_janitor_actions_total` labels `stale_attachment`, `orphan_ip`,
  `drift_repair` count repairs during reconcile (see `ARCHITECTURE.md`).
- **Rollback failures:** `devnest_orchestrator_bringup_rollback_failed_total` when compensating stop
  does not succeed after retries.
- **Durable cleanup queue:** `devnest_cleanup_task_enqueued_total` / `devnest_cleanup_task_attempt_total`
  track persisted cleanup work after failed rollback or partial stop. Pending `workspace_cleanup_task`
  rows are drained from **reconcile** and from **ordinary job-worker ticks** (`drain_pending_cleanup_tasks`),
  so progress does not rely on a single optional control-plane path. Incomplete runtime placement defers
  work with a recorded `deferred_reason` until `WorkspaceRuntime` has a complete node/topology reference.

---

## Health and Readiness

### Liveness

```
GET /health
```

Always returns `200 {"status": "ok"}`. Meant for process supervisors and load-balancer checks.
This endpoint does **not** check downstream dependencies; it only signals the process is alive.

### Readiness

```
GET /ready
```

Performs live dependency checks. Returns `200` when all checks pass:

```json
{
  "status": "ready",
  "checks": {
    "database": "ok",
    "redis": "ok"
  }
}
```

Returns `503` when any check fails, including the failing check names:

```json
{
  "status": "not_ready",
  "failed": ["database"],
  "checks": {
    "database": "connection refused",
    "redis": "ok"
  }
}
```

**Checks performed:**

| Check | Condition |
|---|---|
| `database` | Always — `SELECT 1` on the SQLAlchemy engine |
| `redis` | Only when `DEVNEST_REDIS_URL` is set — `PING` |

Use `/ready` as the Kubernetes readiness probe (or equivalent) to prevent traffic from routing
to unhealthy instances.

---

## Monitoring

### Prometheus Metrics

```
GET /metrics
```

Protected by `X-Internal-API-Key` (infrastructure scope) when `DEVNEST_METRICS_AUTH_ENABLED=true`.

Key metrics to alert on:

| Metric | Alert condition |
|---|---|
| `workspace_jobs_total{status="error"}` rate | High error rate on workspace jobs |
| `devnest_orchestrator_bringup_rollback_total` rate | Spikes in failed bring-ups (probe or exception path); correlate with `orchestrator.bringup.rollback` logs |
| `workspace_reconcile_errors_total` rate | Sustained reconcile failures |
| `http_request_duration_seconds{quantile="0.99"}` | P99 latency above threshold |
| `rate_limit_exceeded_total` | Spike in rate-limit rejections |

### Structured Logging

All services log in structured JSON to stdout. Fields of interest:

| Field | Values | Meaning |
|---|---|---|
| `level` | `INFO`, `WARNING`, `ERROR` | Log severity |
| `service` | `worker`, `reconcile`, `autoscaler` | Source component |
| `workspace_id` | UUID | Per-workspace trace |
| `job_id` | UUID | Per-job trace |
| `error` | exception message | Error detail |
| `devnest_event` | `orchestrator.bringup.rollback` | Compensating cleanup after failed bring-up |

---

## Workspace bring-up and probes (EC2 / VM nodes)

- **Rollback:** Failed bring-up emits `orchestrator.bringup.rollback` and increments `devnest_orchestrator_bringup_rollback_total`. The worker still records the job outcome from `WorkspaceBringUpResult`; the orchestrator is responsible for engine + topology lease cleanup.
- **Probes:** SSH/SSM execution bundles set `service_reachability_runner` so TCP and HTTP readiness run on the **workspace host** (`nc` and `curl`). Do not rely on the API process opening TCP to workspace overlay IPs unless that host is co-located (`DEVNEST_PROBE_ASSUME_COLOCATED_ENGINE=true`, the default for local dev).
- **Reconcile (`ERROR`):** Engine stop + IP lease release runs before gateway orphan deletion so `ERROR` does not imply leaked containers or addresses on the node.

---

## CI and test timeouts

Backend `pytest.ini` sets a default **300s** per-test timeout when `pytest-timeout` is installed (declared in `backend/requirements.txt`). Hanging tests fail fast in CI and locally.

---

## Rate Limiting Monitoring

When `DEVNEST_RATE_LIMIT_BACKEND=redis`:
- The Redis limiter **fails open** on connectivity errors (logs `WARNING` with `redis_rate_limit_fallback`).
- Monitor Redis availability separately — a silent Redis outage degrades rate-limit enforcement.
- Check `/ready` which includes a Redis `PING` to detect Redis failures early.

---

## Autoscaler Operations

### Scale-down overview

The autoscaler uses a two-phase drain:

1. **Mark DRAINING**: an idle `READY` EC2 node is marked `DRAINING`. Nodes with recent workspace
   heartbeat activity within `DEVNEST_AUTOSCALER_RECENT_ACTIVITY_WINDOW_SECONDS` (default 300s) are
   skipped.
2. **Terminate**: On the next autoscaler tick, `DRAINING` nodes that have waited at least
   `DEVNEST_AUTOSCALER_DRAIN_DELAY_SECONDS` (default 30s) since their last state change are terminated.

### Tuning drain delay

Increase drain delay if workspaces are being shut down while users are still active:

```env
DEVNEST_AUTOSCALER_DRAIN_DELAY_SECONDS=120
DEVNEST_AUTOSCALER_RECENT_ACTIVITY_WINDOW_SECONDS=600
```

### Manually protecting a node from scale-down

Mark the node as `BUSY` directly in the database to exclude it from autoscaler selection:

```sql
UPDATE execution_node SET status='BUSY' WHERE id='<node-id>';
```

Revert to `READY` when safe:

```sql
UPDATE execution_node SET status='READY' WHERE id='<node-id>';
```

---

## Workspace Operations

### Workspace lifecycle states

```
PENDING → CREATING → RUNNING → STOPPED → DELETED
                              ↘ ERROR
```

### Force-stop a stuck workspace

If a workspace is stuck in `CREATING` or `RUNNING` with a dead container:

1. Stop the container on the node:
   ```bash
   docker stop devnest-ws-<workspace_id>
   ```
2. Force the workspace to ERROR via direct DB update:
   ```sql
   UPDATE workspace SET status='ERROR' WHERE id='<workspace_id>';
   ```
3. The reconcile loop will pick up the ERROR workspace on the next tick.

### Manually trigger reconcile

```bash
curl -X POST http://api:8000/internal/workspace-reconcile/tick \
  -H "X-Internal-API-Key: $INTERNAL_API_KEY_WORKSPACE_RECONCILE"
```

### Inspect workspace runtime details

```sql
SELECT w.id, w.status, wr.container_id, wr.node_id, wr.workspace_ip
FROM workspace w
LEFT JOIN workspace_runtime wr ON wr.workspace_id = w.id
WHERE w.id = '<workspace_id>';
```

### Workspace persistence

Check that bind mounts are present before starting a workspace:

```bash
ls -la /data/devnest-workspaces/<workspace_id>/
ls -la /data/devnest-workspaces/<workspace_id>/code-server/
```

If the project directory is missing but files should exist, check snapshots:

```bash
GET /workspaces/<id>/snapshots
```

### Clear a stuck workspace job

Jobs stuck in `RUNNING` longer than `WORKSPACE_JOB_STUCK_TIMEOUT_SECONDS` are automatically
reclaimed by the worker. To force-clear manually:

```sql
UPDATE workspace_job
SET status='FAILED', error='manually cleared', updated_at=NOW()
WHERE id='<job_id>' AND status='RUNNING';
```

---

## Snapshot Operations

### Create a snapshot

```bash
curl -X POST http://api:8000/workspaces/<id>/snapshots \
  -H "Authorization: Bearer $TOKEN"
```

### List snapshots

```bash
curl http://api:8000/workspaces/<id>/snapshots \
  -H "Authorization: Bearer $TOKEN"
```

### Restore a snapshot

```bash
curl -X POST http://api:8000/workspaces/<id>/snapshots/<snapshot_id>/restore \
  -H "Authorization: Bearer $TOKEN"
```

Restore is atomic: the original directory is preserved if extraction fails.

### S3 snapshot management

Snapshots stored in S3 under `s3://<DEVNEST_S3_SNAPSHOT_BUCKET>/<DEVNEST_S3_SNAPSHOT_PREFIX>/ws-<id>/`.

List all snapshots for a workspace:

```bash
aws s3 ls s3://<bucket>/<prefix>/ws-<workspace_id>/ --region <region>
```

---

## Database Maintenance

### Backup

Use `pg_dump` on a replica if available:

```bash
pg_dump -Fc devnest > devnest-$(date +%Y%m%d).dump
```

### Apply migrations

```bash
cd backend
alembic upgrade head
```

### Check pending migrations

```bash
alembic current
alembic history --verbose
```

---

## Redis Maintenance

When Redis is configured for distributed rate limiting:

- Use `redis-cli PING` to verify connectivity.
- Rate-limiter keys are `ratelimit:<ip>:<limiter_name>` sorted sets with TTL equal to the window.
- Flush all rate-limit keys (e.g. after a Redis restart):
  ```bash
  redis-cli KEYS "ratelimit:*" | xargs redis-cli DEL
  ```
- Redis is not used for primary data storage — clearing all keys only temporarily resets the rate-limit windows.

---

## Logs and Debugging

### View worker logs (in-process)

Worker logs are part of the main API log stream.

### View container logs for a workspace

```bash
docker logs devnest-ws-<workspace_id> --follow
```

### View orchestrator reconcile logs

Filter by `service=reconcile`:

```bash
journalctl -u devnest-api | jq 'select(.service=="reconcile")'
```

### Check autoscaler decisions

Filter by `service=autoscaler`:

```bash
journalctl -u devnest-api | jq 'select(.service=="autoscaler")'
```

For Phase 2 EC2 scale-out, `/internal/autoscaler/evaluate` reports all missing launch settings in
`decision.reasons`. A launch-capable environment needs:

```bash
AWS_REGION=us-east-1
DEVNEST_AUTOSCALER_ENABLED=true
DEVNEST_AUTOSCALER_EVALUATE_ONLY=false
DEVNEST_AUTOSCALER_MAX_NODES=3
DEVNEST_AUTOSCALER_MAX_CONCURRENT_PROVISIONING=1
DEVNEST_EC2_AMI_ID=ami-...
DEVNEST_EC2_INSTANCE_TYPE=t3.medium
DEVNEST_EC2_SUBNET_ID=subnet-...
DEVNEST_EC2_SECURITY_GROUP_IDS=sg-...
DEVNEST_EC2_INSTANCE_PROFILE=DevNestExecutionNodeProfile
DEVNEST_EC2_DEFAULT_EXECUTION_MODE=ssm_docker
DEVNEST_EC2_TAG_PREFIX=devnest
DEVNEST_EC2_EXTRA_TAGS=env=prod,service=execution-node
DEVNEST_EC2_WORKSPACE_PROJECTS_BASE=/var/lib/devnest/workspace-projects
DEVNEST_EC2_BOOTSTRAP_PREBAKED=false
DEVNEST_EC2_HEARTBEAT_INTERNAL_API_BASE_URL=http://api.internal.example:8000
INTERNAL_API_KEY_INFRASTRUCTURE=<strong-random-infra-key>
# Optional custom override; leave empty for generated Amazon Linux 2023 bootstrap.
# Custom user-data may include {{NODE_KEY}} or {{DEVNEST_NODE_KEY}} placeholders.
DEVNEST_EC2_USER_DATA_B64=
# Or, only for AMIs that already start Docker + heartbeat instead of generated bootstrap:
# DEVNEST_EC2_BOOTSTRAP_PREBAKED=true
```

Trigger one scale-out tick:

```bash
curl -X POST -H "X-Internal-API-Key: $INTERNAL_API_KEY_AUTOSCALER" \
  "$INTERNAL_API_BASE_URL/internal/autoscaler/provision-one"
```

Verify generated Amazon Linux 2023 bootstrap on the new EC2 node:

```bash
docker --version
systemctl status docker --no-pager
test -d /opt/devnest
test -d /var/lib/devnest/workspace-projects
systemctl status devnest-execution-node-heartbeat --no-pager
journalctl -u devnest-execution-node-heartbeat -n 50 --no-pager
tail -n 100 /var/log/devnest/bootstrap.log
```
