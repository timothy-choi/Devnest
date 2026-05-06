# Chaos and fault-injection helpers

Scripts in this directory **automate or sketch** infrastructure faults so operators can validate DevNest recovery behavior.

**Authoritative narrative, expected signals, and limitations:** see [`RELIABILITY.md`](../../RELIABILITY.md) at the repository root.

## Scripts

| Script | Scenario |
|--------|----------|
| [`kill_workspace_worker.sh`](./kill_workspace_worker.sh) | Worker process/container killed during workspace **CREATING** / job **RUNNING** |
| [`restart_docker_execution_host.sh`](./restart_docker_execution_host.sh) | Docker daemon restart on an execution host |
| [`terminate_ec2_execution_node.sh`](./terminate_ec2_execution_node.sh) | Abrupt EC2 instance termination (`CONFIRM=yes` required) |
| [`simulate_ssm_failure.sh`](./simulate_ssm_failure.sh) | SSM command failure runbook (IAM / network / tests) |
| [`simulate_s3_snapshot_failure.sh`](./simulate_s3_snapshot_failure.sh) | S3 snapshot failure runbook |

[`common.sh`](./common.sh) is library-only (source from other bash scripts).

## Safety

- Prefer **staging** or dedicated chaos namespaces.
- EC2 termination is **irreversible** without automation reprovisioning the instance.
- Restarting Docker kills **all** containers on that host.

## Quick verification after a drill

```bash
# Example: drain queued work after worker kill (replace API URL + key)
curl -sS -X POST "http://localhost:8000/internal/workspace-jobs/process?limit=10" \
  -H "X-Internal-API-Key: ${INTERNAL_API_KEY}"

# Optional: enqueue reconcile for a workspace
curl -sS -X POST "http://localhost:8000/internal/workspaces/123/reconcile-runtime" \
  -H "X-Internal-API-Key: ${INTERNAL_API_KEY}"
```

Structured log grep examples live in `RELIABILITY.md`.
