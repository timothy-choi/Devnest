# Runbook: Undrain an execution node

**Goal:** Put a node back into the **schedulable** pool (**READY** + **`schedulable=true`**) so the scheduler can place new workspaces on it.

## When to use

- Incident cleared after a [drain](./RUNBOOK_EXECUTION_NODE_DRAIN.md).
- Catalog row was **`schedulable=false`** for maintenance but **`status`** is still **READY** or **DRAINING**.

## Steps

1. Confirm root cause is fixed (SSM/SSH, Docker, disk, network).

2. **Undrain:**
   ```bash
   curl -sS -X POST -H "X-Internal-API-Key: $INTERNAL_API_KEY" -H "Content-Type: application/json" \
     -d '{"node_key":"<NODE_KEY>"}' \
     "$DEVNEST_API_BASE/internal/execution-nodes/undrain"
   ```
   Or: `./scripts/devnest_ops_nodes.sh undrain '{"node_key":"<NODE_KEY>"}'`

3. **Expect** HTTP **200** with **`status=READY`**, **`schedulable=true`**.

4. **409 / error:** Node may be **TERMINATED** — re-register or provision a new row; **NOT_READY** / **PROVISIONING** may need **`POST /internal/execution-nodes/sync`** first.

## Verify

- `GET /internal/execution-nodes/` shows the node with fresh **`heartbeat_age_seconds`** if agents run.
- Create a test workspace and confirm **`scheduler.node.selected`** (logs) can pick this node when capacity and spread allow.
