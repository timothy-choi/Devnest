# Runbook: Deregister an execution node (catalog)

**Goal:** **Soft-remove** the node from DevNest scheduling (**TERMINATED**, not schedulable). This does **not** terminate the EC2 instance or delete volumes.

## Preconditions

- No production reliance: stop or move workspaces whose **`workspace_runtime.node_id`** matches this **`node_key`**.
- Prefer [drain](./RUNBOOK_EXECUTION_NODE_DRAIN.md) first so new work does not land on the host while you clean up.

## Steps

1. Confirm **`workspaces-by-node`** count is **0** (or acceptable) for this **`node_key`**.

2. **Deregister:**
   ```bash
   curl -sS -X POST -H "X-Internal-API-Key: $INTERNAL_API_KEY" -H "Content-Type: application/json" \
     -d '{"node_key":"<NODE_KEY>"}' \
     "$DEVNEST_API_BASE/internal/execution-nodes/deregister"
   ```
   Or: `./scripts/devnest_ops_nodes.sh deregister '{"node_key":"<NODE_KEY>"}'`

3. **Verify:** List shows **`status=TERMINATED`**, **`schedulable=false`**. **Undrain** is not valid for TERMINATED rows.

4. **EC2:** If the instance should be destroyed, use your cloud process (console/Terraform); DevNest does not auto-terminate here.

## Rollback

- Register again via **`POST /internal/execution-nodes/register-existing`** (or provisioning flow), **`POST /sync`**, then heartbeat smoke — see fleet runbook.
