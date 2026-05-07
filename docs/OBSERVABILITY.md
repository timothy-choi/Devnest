# DevNest observability

This document describes **metrics**, **local Prometheus/Grafana**, and **dashboards** for autoscaling, scheduling, and reliability.

## Metrics endpoint

The FastAPI app exposes Prometheus text exposition at:

`GET /metrics`

Optional protection: set `DEVNEST_METRICS_AUTH_ENABLED=true` and send `X-Internal-API-Key` with an infrastructure-scoped internal key (see application settings).

Each scrape refreshes queue depth, workspace/node counts, and per-node disk/memory gauges from the database before rendering the registry.

## Starting Prometheus and Grafana (Docker Compose)

Integration Compose includes Prometheus and Grafana with provisioning wired to the repo:

```bash
docker compose -f docker-compose.integration.yml up -d prometheus grafana backend
```

- **Prometheus** scrapes the `backend` service at `http://backend:8000/metrics` (see `prometheus/prometheus.yml`).
- **Grafana** loads dashboards from `observability/grafana/dashboards/` and the Prometheus datasource from `observability/grafana/provisioning/datasources/`.

Default Grafana login is often `admin` / `admin` on first boot (change in production).

## Key metrics (production-oriented)

| Metric | Type | Purpose |
|--------|------|---------|
| `devnest_workspace_created_total` | Counter | Create intents accepted (`workspace_status`, `provider_type`) |
| `devnest_workspace_failed_total` | Counter | Terminal job failures (`workspace_status`, `failure_reason`, `node_key`, `provider_type`) |
| `devnest_workspace_retried_total` | Counter | Jobs requeued for retry / capacity / node readiness |
| `devnest_autoscaler_scale_up_total` | Counter | Scale-up provisions (`provider_type`) |
| `devnest_autoscaler_scale_down_total` | Counter | Scale-down terminate path (`provider_type`) |
| `devnest_node_cleanup_total` | Counter | Topology janitor, durable cleanup tasks, EC2 orphan janitor (`action`) |
| `devnest_chaos_recovery_total` | Counter | Job success with `attempt >= 2` (`recovery_type`, `job_type`) |
| `devnest_active_workspaces` | Gauge | Workspaces in `RUNNING` |
| `devnest_ready_nodes` | Gauge | Nodes in `READY` |
| `devnest_provisioning_nodes` | Gauge | Nodes in `PROVISIONING` |
| `devnest_draining_nodes` | Gauge | Nodes in `DRAINING` |
| `devnest_pending_workspace_jobs` | Gauge | Jobs in `QUEUED` |
| `devnest_node_disk_free_mb` | Gauge | Last heartbeat disk MiB (`node_key`, `provider_type`) |
| `devnest_node_memory_free_mb` | Gauge | Last heartbeat memory MiB (`node_key`, `provider_type`) |
| `devnest_workspace_provision_seconds` | Histogram | CREATE/START bring-up duration (`job_type`, `workspace_status`, `failure_reason`) |
| `devnest_node_bootstrap_seconds` | Histogram | EC2 provision metadata → READY (`node_key`, `provider_type`, `readiness`) |
| `devnest_scale_down_seconds` | Histogram | Drain + terminate wall time (`node_key`, `provider_type`) |
| `devnest_ssm_command_seconds` | Histogram | SSM RunShellScript latency (`command_family`) |

Legacy series such as `devnest_workspace_provisioning_duration_seconds`, `devnest_queue_depth`, and `devnest_execution_nodes` remain useful for older dashboards.

## Sample PromQL

**Autoscaler events (per second, 5m window):**

```promql
sum by (provider_type) (rate(devnest_autoscaler_scale_up_total[5m]))
```

```promql
sum by (provider_type) (rate(devnest_autoscaler_scale_down_total[5m]))
```

**Nodes by status (instant):**

```promql
sum by (status, provider_type) (devnest_execution_nodes)
```

**Workspace provisioning latency (p95, new histogram):**

```promql
histogram_quantile(
  0.95,
  sum by (le) (rate(devnest_workspace_provision_seconds_bucket[5m]))
)
```

**Retries and failures:**

```promql
sum by (failure_reason) (rate(devnest_workspace_retried_total[5m]))
```

```promql
sum by (failure_reason) (rate(devnest_workspace_failed_total[5m]))
```

**Janitor / cleanup:**

```promql
sum by (action) (rate(devnest_node_cleanup_total[5m]))
```

**Disk free per node (MiB):**

```promql
devnest_node_disk_free_mb
```

## Grafana dashboards

- `observability/grafana/dashboards/devnest-overview.json` — fleet overview.
- `observability/grafana/dashboards/devnest-autoscaling-reliability.json` — autoscaling, node states, provisioning latency, resources per node, retries/failures, cleanup.

Import manually if not using Compose provisioning: **Dashboards → Import → Upload JSON**.

## Screenshots (placeholders)

Add screenshots here after you run Grafana against a live environment:

1. **Autoscaling** — scale up/down rates and autoscaler decisions.
2. **Nodes** — `devnest_execution_nodes` or ready/provisioning/draining gauges.
3. **Provisioning** — histogram quantiles for workspace bring-up.
4. **Resources** — disk/memory gauges per node.
5. **Reliability** — workspace failures, retries, chaos recovery, cleanup counters.

<!-- Screenshot: autoscaling panels -->
<!-- Screenshot: node capacity -->
<!-- Screenshot: provisioning latency -->
<!-- Screenshot: disk/memory per node -->
<!-- Screenshot: failures/retries/cleanup -->
