# Phase 3b Step 12 — Ops hardening (multi-node execution fleet)

**Goal:** Operable, observable fleet behavior without ECS, autoscaling redesign, or storage architecture changes.

**Related:** [Fleet runbook](./PHASE_3B_FLEET_RUNBOOK.md), [Step 11 — scheduling spread](./PHASE_3B_STEP11_TWO_NODE_SCHEDULING_SPREAD.md), [WORKSPACE_NODES.md](./WORKSPACE_NODES.md), [Execution node heartbeat](./EXECUTION_NODE_HEARTBEAT.md).

---

## 1. Internal API (infrastructure scope)

All routes require **`X-Internal-API-Key`** with infrastructure scope (same as execution-node mutations).

| Operation | Method | Path | Notes |
|-----------|--------|------|-------|
| **List nodes + capacity** | `GET` | `/internal/execution-nodes/` | Per-node `active_workspace_slots`, `available_workspace_slots`, **`heartbeat_age_seconds`**, allocatable resources. |
| **Workspaces per node** | `GET` | `/internal/execution-nodes/workspaces-by-node?limit_per_node=50` | Groups by `workspace_runtime.node_id`; includes orphan `node_id` values not in `execution_node`. |
| **Drain** | `POST` | `/internal/execution-nodes/drain` | Body: `{"node_key":"..."}` or `{"node_id": 2}`. Sets **DRAINING** + **`schedulable=false`**. |
| **Undrain** | `POST` | `/internal/execution-nodes/undrain` | Same body. **DRAINING** → **READY** + **`schedulable=true`**; or **READY** + `schedulable=false` → `true`. **409** if **TERMINATED** / wrong lifecycle. |
| **Deregister** | `POST` | `/internal/execution-nodes/deregister` | Soft-remove from scheduling (**TERMINATED** + not schedulable). Does **not** stop EC2. |
| **Sync** | `POST` | `/internal/execution-nodes/sync` | EC2 describe + optional promote **PROVISIONING** → **READY**. |
| **Heartbeat** | `POST` | `/internal/execution-nodes/heartbeat` | Agent/worker liveness + optional `disk_free_mb`, `slots_in_use`. |

**Script:** `scripts/devnest_ops_nodes.sh` wraps `curl` + `jq` for list / workspaces / drain / undrain.

---

## 2. Runbooks

### 2.1 Drain a node (stop new placements)

1. Confirm workloads: `GET /internal/execution-nodes/workspaces-by-node` — note `workspace_count` for the target `node_key`.  
2. `POST /internal/execution-nodes/drain` with `node_key` or `node_id`.  
3. Verify: `GET /internal/execution-nodes/` — row shows **`status=DRAINING`**, **`schedulable=false`**.  
4. New **creates** avoid this node; **running** workspaces stay until stopped or migrated (no automatic eviction in V1).

### 2.2 Undrain a node

1. Resolve why the node was drained (incident cleared).  
2. `POST /internal/execution-nodes/undrain` with the same selector.  
3. Expect **200** with **`status=READY`**, **`schedulable=true`**.  
4. If **409**: node may be **TERMINATED** — re-register via `register-existing` or restore from backup; **NOT_READY** / **PROVISIONING** → use **`POST /sync`** first.

### 2.3 Deregister a node (catalog soft-remove)

1. Ensure no production dependency on the row (workspaces stopped or moved).  
2. `POST /internal/execution-nodes/deregister`.  
3. Row becomes **TERMINATED** + not schedulable. **Undrain is not valid** — see 2.2.

### 2.4 Recover a node (after incident / new instance)

1. **EC2 replacement:** register the new instance (`POST /internal/execution-nodes/register-existing`), **`POST /sync`**, heartbeat smoke.  
2. **Same row, DRAINING:** `undrain` when healthy.  
3. **Stale NOT_READY:** fix SSM/network, then **`POST /sync`** with `promote_provisioning_when_ready` as appropriate.

### 2.5 Node heartbeat stale

1. When **`DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT=true`**, placement excludes nodes older than **`DEVNEST_NODE_HEARTBEAT_MAX_AGE_SECONDS`**.  
2. Ops: check **`heartbeat_age_seconds`** on `GET /internal/execution-nodes/`.  
3. Fix agent/worker posting **`POST /internal/execution-nodes/heartbeat`** or **`DEVNEST_NODE_HEARTBEAT_ENABLED`** on the worker side.  
4. Logs: heartbeat receive/update use `execution_node_heartbeat_received` / `execution_node_heartbeat_node_updated` (module logger).

### 2.6 Node disk full

1. Heartbeat may report **`disk_free_mb`** under `metadata_json.heartbeat`.  
2. Reduce local workspace data, expand volume, or drain the node and shift new creates to peers.  
3. Placement still uses **`allocatable_disk_mb`** minus runtime reservations — full disk on instance may surface as bring-up/export failures; check **`workspace.job.failed`** and orchestrator issues.

### 2.7 Failed workspace on one node

1. Identify node: `GET /internal/execution-nodes/workspaces-by-node` or DB `workspace_runtime.node_id`.  
2. User flows: **stop** / **delete** workspace; optional **`POST /workspaces/{id}/reconcile-runtime`** if product exposes it.  
3. If gateway stuck: reconcile path may **deregister** route; logs **`gateway.route.deregistered`** / **`reconcile.*`**.  
4. Node-specific Docker/SSM issues: use **`POST /internal/execution-nodes/smoke-read-only`** for EC2 reachability.

---

## 3. Diagnostics (structured logs)

Search by **`devnest_event`** (message) or your JSON `extra` field names.

| Topic | Event / signal | Key `extra` fields |
|-------|----------------|-------------------|
| **Placement** | `scheduler.node.selected` | `workspace_id`, `execution_node_id`, `node_key`, `requested_*`, `multi_node_scheduling_enabled`, `placement_single_node_gate` |
| | `placement.decision.summary` | `placement_summary` (single line, truncated digest of pool + effective free + sort policy) |
| | `placement.no_schedulable_node` | `detail` (truncated), same gate flags |
| **Gateway route target** | `gateway.route.registered` | `gateway_upstream_target`, **`gateway_route_target`** (same value; Traefik upstream), `node_key`, `execution_node_id`, `workspace_id`, `public_host` |
| **Snapshot source** | `snapshot.storage.upload.started` / `.succeeded` | **`source_node_key`**, **`source_execution_node_id`** (when known) |

---

## 4. Rollback (fleet stays on node 1)

1. **Drain** secondary node: `POST /internal/execution-nodes/drain` with its `node_key`.  
2. **Disable multi-node scheduling:** set **`DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=false`** on API + worker; restart — new placements use **primary** (`min(execution_node.id)` among READY+schedulable). See [Step 7](./PHASE_3B_STEP7_MULTI_NODE_SCHEDULING_FLAG.md).  
3. **Keep node 1 serving:** ensure node 1 is **READY**, **`schedulable=true`**, heartbeats healthy if gating is on.

---

## 5. Verification checklist

- [ ] `GET /internal/execution-nodes/` returns **`heartbeat_age_seconds`** and slot counts.  
- [ ] `GET /internal/execution-nodes/workspaces-by-node` matches expectations for test workspaces.  
- [ ] `POST /drain` then `POST /undrain` on a non-production node restores **READY** + **schedulable**.  
- [ ] `POST /undrain` on a **TERMINATED** node returns **409**.  
- [ ] Create workspace → logs contain **`scheduler.node.selected`** and **`placement.decision.summary`**.  
- [ ] Running workspace with gateway → after register, **`gateway.route.registered`** includes **`gateway_route_target`**.  
- [ ] Snapshot upload path logs **`source_node_key`** / **`source_execution_node_id`** on upload started/succeeded.  
- [ ] `./scripts/devnest_ops_nodes.sh list` against a dev API (with `INTERNAL_API_KEY` + `DEVNEST_API_BASE`).

---

## 6. Explicit non-goals (Step 12)

- No **ECS** integration.  
- No **autoscaler** behavior changes (existing flags stay as-is).  
- No **redesign** of workspace project storage or S3 layout.
