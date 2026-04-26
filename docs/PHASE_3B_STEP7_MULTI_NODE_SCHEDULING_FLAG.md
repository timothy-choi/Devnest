# Phase 3b Step 7 — Multi-node scheduling safety flag

Operator and engineering reference for **`DEVNEST_ENABLE_MULTI_NODE_SCHEDULING`**. No route-admin or Traefik changes; this step only defines **control-plane placement pool** behavior.

**Related:** [Phase 3b fleet runbook](./PHASE_3B_FLEET_RUNBOOK.md), [Step 5 — Node 2 heartbeat](./PHASE_3B_STEP5_HEARTBEAT_NODE2.md), Step 6 internal read-only execution-node smoke (operator API).

---

## 1. Environment variable

| Variable | Default | Meaning |
|----------|---------|---------|
| `DEVNEST_ENABLE_MULTI_NODE_SCHEDULING` | `false` | When **false**, new workspace placement considers **only the primary execution node**. When **true**, all **READY** + **`schedulable=true`** nodes in the provider pool are candidates (subject to capacity, slots, heartbeat gate, etc.). |

Pydantic also accepts `devnest_enable_multi_node_scheduling`. Truthy strings: `1`, `true`, `yes`, `on` (case-insensitive).

---

## 2. Primary node when the flag is off

Among rows that are **READY**, **`schedulable=true`**, and pass **`DEVNEST_NODE_PROVIDER`** (`local` / `ec2` / `all`), the **primary** node is the one with the **lowest `execution_node.id`**.

- **Fleet node 2** (or any additional node) is **never** chosen for **new** placements while the flag is **false**, even if an operator temporarily sets **`schedulable=true`** on node 2.
- **Node 2 is used** only when **`DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=true`** and node 2 remains **READY** + **`schedulable=true`** (and passes capacity / heartbeat rules). Until then, keep node 2 at **`schedulable=false`** for an extra belt-and-suspenders guard.

**Ordering note:** Primary is tied to **database insert order** (lowest id), not to `node_key` names. In typical Phase 3b rollouts, node 1 is registered first, so its id is lower than node 2’s.

---

## 3. Behavior summary

### Flag **off** (default)

- Placement pool = **at most one** execution node (the primary by min `id`).
- Autoscaler helpers that count “READY+schedulable EC2” for placement-aligned decisions use the **same** pool (so scale-up / idle detection does not assume capacity on nodes that scheduling will never use).
- **No** Traefik or route-admin changes.
- Existing workspaces already pinned to any node are unchanged; this gate applies to **new** selection via `select_node_for_workspace` / `reserve_node_for_workspace`.

### Flag **on**

- Placement pool = all qualifying nodes (existing multi-node sort: effective CPU, memory, active workload spread, `node_key` tiebreak).
- Secondary nodes participate only if **READY** and **`schedulable=true`** (and other existing gates).

---

## 4. Placement logging requirements

Structured logs use `log_event` with stable `devnest_event` names.

| Event | Required / recommended fields |
|-------|-------------------------------|
| **`scheduler.node.selected`** | `workspace_id`, `execution_node_id`, `node_key`, `requested_cpu`, `requested_memory_mb`, `requested_disk_mb`, **`multi_node_scheduling_enabled`** (bool), **`placement_single_node_gate`** (bool, true when the single-node gate is active). |
| **`placement.no_schedulable_node`** | Same **`multi_node_scheduling_enabled`** and **`placement_single_node_gate`**, plus `detail` (truncated message, no secrets). |

Human-readable **`explain_placement_decision`** text includes a short note when the single-node gate narrows the pool.

`NoSchedulableNodeError` messages append a pointer to this doc when the gate is on.

---

## 5. Verification checklist

1. **Default / flag off**  
   - With two **READY** + **`schedulable=true`** EC2 nodes in the catalog, create a new workspace; it should land on the **primary** (lowest `execution_node.id`).  
   - Confirm logs: `scheduler.node.selected` includes `placement_single_node_gate=true`, `multi_node_scheduling_enabled=false`.

2. **Node 2 ignored when flag off**  
   - Set node 2 **`schedulable=true`** (test only); new workspaces must **still** schedule only on the primary while the flag remains **false**.

3. **Flag on**  
   - Set `DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=true` on API + worker; restart.  
   - With both nodes **READY** + **`schedulable=true`**, new placements may use node 2 per capacity / spread policy.  
   - Logs show `multi_node_scheduling_enabled=true`, `placement_single_node_gate=false`.

4. **Node 2 off pool**  
   - With flag **on**, set node 2 **`schedulable=false`**; new workspaces must not bind to node 2 (existing policy).

5. **Regression**  
   - Smoke: create / open / save / download on a workspace on node 1.  
   - No route-admin-only changes required for this step.

6. **Unit tests**  
   - `pytest backend/tests/unit/placement_service/test_node_placement.py backend/tests/unit/autoscaler/ -q`

---

## 6. Rollback

- Set `DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=false` (or unset).  
- Restart API and workspace job worker.  
- No database migration; no ingress rollback.

---

## 7. Enablement order (recommended)

1. Node 2 registered, heartbeat healthy, **read-only smoke** OK (Step 6).  
2. Keep **`schedulable=false`** on node 2 until you intend to place workloads.  
3. Set **`DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=true`** only when ready for multi-node placement.  
4. Set node 2 **`schedulable=true`** when ready to admit workloads on node 2.
