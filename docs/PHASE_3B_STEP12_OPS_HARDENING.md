# Phase 3b Step 12 — Ops hardening (multi-node execution fleet)

**Goal:** Operable, observable fleet behavior **without** ECS, autoscaling redesign, or storage architecture changes.

**Related:** [Fleet runbook](./PHASE_3B_FLEET_RUNBOOK.md), [Step 11 — scheduling spread](./PHASE_3B_STEP11_TWO_NODE_SCHEDULING_SPREAD.md), [WORKSPACE_NODES.md](./WORKSPACE_NODES.md), [Execution node heartbeat](./EXECUTION_NODE_HEARTBEAT.md), **[Operator runbooks index](./runbooks/README.md)**.

---

## 1. Internal API (infrastructure scope)

All routes require **`X-Internal-API-Key`** with infrastructure scope (same as execution-node mutations).

| Operation | Method | Path | Notes |
|-----------|--------|------|-------|
| **List nodes + capacity** | `GET` | `/internal/execution-nodes/` | Per-node **`heartbeat_age_seconds`**, `active_workspace_slots`, `available_workspace_slots`, allocatable resources. |
| **Workspaces per node** | `GET` | `/internal/execution-nodes/workspaces-by-node?limit_per_node=50` | Groups by `workspace_runtime.node_id`; includes orphan `node_id` values not in `execution_node`. |
| **Drain** | `POST` | `/internal/execution-nodes/drain` | Body: `{"node_key":"..."}` or `{"node_id": 2}`. Sets **DRAINING** + **`schedulable=false`**. |
| **Undrain** | `POST` | `/internal/execution-nodes/undrain` | Same body. **DRAINING** → **READY** + **`schedulable=true`**; or **READY** + `schedulable=false` → `true`. **409** if **TERMINATED** / wrong lifecycle. |
| **Deregister** | `POST` | `/internal/execution-nodes/deregister` | Soft-remove from scheduling (**TERMINATED** + not schedulable). Does **not** stop EC2. |
| **Sync** | `POST` | `/internal/execution-nodes/sync` | EC2 describe + optional promote **PROVISIONING** → **READY**. |
| **Heartbeat** | `POST` | `/internal/execution-nodes/heartbeat` | Agent/worker liveness + optional `disk_free_mb`, `slots_in_use`. Emits **`execution.node.heartbeat_recorded`**. |

**CLI:** `scripts/devnest_ops_nodes.sh` — `list`, `workspaces`, `drain`, `undrain`, `deregister`, `heartbeat`, `smoke`.

---

## 2. Runbooks (step-by-step)

| Procedure | Document |
|-----------|----------|
| Drain node (stop new placements) | [runbooks/RUNBOOK_EXECUTION_NODE_DRAIN.md](./runbooks/RUNBOOK_EXECUTION_NODE_DRAIN.md) |
| Undrain node | [runbooks/RUNBOOK_EXECUTION_NODE_UNDRAIN.md](./runbooks/RUNBOOK_EXECUTION_NODE_UNDRAIN.md) |
| Deregister node (catalog) | [runbooks/RUNBOOK_EXECUTION_NODE_DEREGISTER.md](./runbooks/RUNBOOK_EXECUTION_NODE_DEREGISTER.md) |
| Recover stale heartbeat | [runbooks/RUNBOOK_STALE_EXECUTION_NODE_HEARTBEAT.md](./runbooks/RUNBOOK_STALE_EXECUTION_NODE_HEARTBEAT.md) |
| Disk-full node | [runbooks/RUNBOOK_DISK_FULL_EXECUTION_NODE.md](./runbooks/RUNBOOK_DISK_FULL_EXECUTION_NODE.md) |
| Failed workspace on one node | [runbooks/RUNBOOK_FAILED_WORKSPACE_ON_EXECUTION_NODE.md](./runbooks/RUNBOOK_FAILED_WORKSPACE_ON_EXECUTION_NODE.md) |

---

## 3. Diagnostics (structured logs / `devnest_event`)

Search by **`devnest_event`** (message) or JSON `extra` field names.

| Topic | Event | Key `extra` fields |
|-------|--------|-------------------|
| **Placement** | `scheduler.node.selected` | `workspace_id`, `execution_node_id`, `node_key`, `requested_*`, `multi_node_scheduling_enabled`, `placement_single_node_gate`, `placement_reason`, **`target_node_heartbeat_age_seconds`** |
| | `placement.decision.summary` | `placement_summary`, **`target_node_heartbeat_age_seconds`**, `node_key`, `execution_node_id` |
| | `placement.no_schedulable_node` | `detail`, gate flags, **`heartbeat_gate_enabled`**, **`node_heartbeat_max_age_seconds`** |
| **Heartbeat** | `execution.node.heartbeat_recorded` | `node_key`, `execution_node_id`, **`heartbeat_age_seconds`**, `docker_ok`, `disk_free_mb`, `slots_in_use` |
| **Gateway route target** | `gateway.route.registered` | `gateway_upstream_target`, `gateway_route_target`, `node_key`, `execution_node_id`, `workspace_id`, `public_host` |
| **Snapshot source** | `snapshot.storage.upload.started` / `.succeeded` | **`source_node_key`**, **`source_execution_node_id`** (when known) |

---

## 4. Rollback (keep primary serving; optional secondary off pool)

1. **Drain** secondary execution node: `POST /internal/execution-nodes/drain` with its `node_key` (or `./scripts/devnest_ops_nodes.sh drain …`). New placements skip it; **running** workspaces stay until stopped.
2. **Disable multi-node scheduling** (primary-only pool): set **`DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=false`** on **API** and **workspace job worker**; restart. New placements use **primary** (`min(execution_node.id)` among READY+schedulable after provider filter). See [Step 7](./PHASE_3B_STEP7_MULTI_NODE_SCHEDULING_FLAG.md). *(Integration Compose may default multi-node **on** — override explicitly.)*
3. **Keep primary serving:** ensure primary row is **READY**, **`schedulable=true`**, and heartbeats meet **`DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT`** when that flag is on.

---

## 5. Verification checklist

- [ ] `GET /internal/execution-nodes/` returns **`heartbeat_age_seconds`** and slot counts.
- [ ] `GET /internal/execution-nodes/workspaces-by-node` matches expectations.
- [ ] `POST /drain` then `POST /undrain` on a non-production node restores **READY** + **schedulable**.
- [ ] `POST /undrain` on a **TERMINATED** node returns **409**.
- [ ] `POST /deregister` soft-terminates catalog row (verify list).
- [ ] Create workspace → logs **`scheduler.node.selected`** with **`target_node_heartbeat_age_seconds`**.
- [ ] **`placement.decision.summary`** includes **`target_node_heartbeat_age_seconds`**.
- [ ] Heartbeat POST → **`execution.node.heartbeat_recorded`** with **`heartbeat_age_seconds`** (~0 after success).
- [ ] **`placement.no_schedulable_node`** (when forced) includes **`heartbeat_gate_enabled`** when gating is configured.
- [ ] Gateway: **`gateway.route.registered`** includes **`gateway_upstream_target`**.
- [ ] Snapshot: upload logs include **`source_node_key`** / **`source_execution_node_id`**.
- [ ] `./scripts/devnest_ops_nodes.sh list` with `INTERNAL_API_KEY` + `DEVNEST_API_BASE`.

---

## 6. Explicit non-goals (Step 12)

- No **ECS** integration.  
- No **autoscaler** behavior changes.  
- No **redesign** of workspace project storage or S3 layout.
