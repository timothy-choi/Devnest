# Runbook: Drain an execution node

**Goal:** Exclude a host from **new** workspace placement while leaving **running** workloads untouched (V1 has no automatic eviction).

## Preconditions

- API URL and **`INTERNAL_API_KEY`** (infrastructure scope).
- Know **`node_key`** or **`execution_node.id`** from `GET /internal/execution-nodes/`.

## Steps

1. **Inventory** workloads on the node:
   ```bash
   curl -sS -H "X-Internal-API-Key: $INTERNAL_API_KEY" \
     "$DEVNEST_API_BASE/internal/execution-nodes/workspaces-by-node?limit_per_node=100"
   ```
   Or: `./scripts/devnest_ops_nodes.sh workspaces 100`

2. **Drain** (sets **DRAINING** + **`schedulable=false`**):
   ```bash
   curl -sS -X POST -H "X-Internal-API-Key: $INTERNAL_API_KEY" -H "Content-Type: application/json" \
     -d '{"node_key":"<NODE_KEY>"}' \
     "$DEVNEST_API_BASE/internal/execution-nodes/drain"
   ```
   Or: `./scripts/devnest_ops_nodes.sh drain '{"node_key":"<NODE_KEY>"}'`

3. **Verify** list output: `status=DRAINING`, `schedulable=false`, `heartbeat_age_seconds` still updated if agents post heartbeats.

4. **Placement:** New creates skip this node; scheduler uses other **READY** + **`schedulable=true`** rows.

## Rollback / next

- [Undrain](./RUNBOOK_EXECUTION_NODE_UNDRAIN.md) when the host is healthy again.
- If the node must leave the fleet permanently, follow [Deregister](./RUNBOOK_EXECUTION_NODE_DEREGISTER.md) after workloads are gone.
