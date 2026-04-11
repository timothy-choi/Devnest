# Reconcile service (V1)

Conservative **RECONCILE_RUNTIME** jobs compare **settled** `Workspace.status` to observed orchestrator health and optional gateway routes. The control plane remains authoritative; the data plane is repaired only when drift is unambiguous.

## Allowed enqueue statuses

`RUNNING`, `STOPPED`, `ERROR`, `DELETED` — not while `CREATING` / `STARTING` / `*ING` busy.

## Repairs

| Status   | Drift                         | Action                                      |
|----------|-------------------------------|---------------------------------------------|
| RUNNING  | Health not successful         | Job failed; workspace → `ERROR`             |
| RUNNING  | Healthy; DB snapshot stale    | Sync `WorkspaceRuntime` from health         |
| RUNNING  | Healthy; gateway route wrong/missing | Re-register route (`DEVNEST_GATEWAY_ENABLED`) |
| STOPPED  | Container still running       | `stop_workspace_runtime` (same finalize as STOP job) |
| STOPPED  | Orphan gateway route          | Deregister                                  |
| ERROR    | Orphan gateway route          | Deregister                                  |
| DELETED  | Orphan gateway route          | Deregister                                  |

## Deferred

Auto bring-up for missing RUNNING runtime, cron/leader reconciliation, EC2/Kubernetes, distributed locks, TLS/DNS, aggressive multi-step healing.

## Trigger

`POST /internal/workspaces/{workspace_id}/reconcile-runtime` (internal API key), then process queue via existing `/internal/workspace-jobs/process`.
