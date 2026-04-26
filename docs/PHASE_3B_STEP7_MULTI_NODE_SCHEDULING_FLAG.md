# Phase 3b Step 7 — Multi-node scheduling safety flag

Operator and engineering reference for **`DEVNEST_ENABLE_MULTI_NODE_SCHEDULING`**. No route-admin or Traefik changes; this step only defines **control-plane placement pool** behavior.

**Related:** [Phase 3b fleet runbook](./PHASE_3B_FLEET_RUNBOOK.md), [Step 11 — Two-node spread](./PHASE_3B_STEP11_TWO_NODE_SCHEDULING_SPREAD.md), [Step 5 — Node 2 heartbeat](./PHASE_3B_STEP5_HEARTBEAT_NODE2.md), Step 6 internal read-only execution-node smoke (operator API).

---

## 1. Environment variable

| Variable | Default | Meaning |
|----------|---------|---------|
| `DEVNEST_ENABLE_MULTI_NODE_SCHEDULING` | `true` (Phase 3b Step 11+) | When **false**, new workspace placement considers **only the primary execution node** (lowest `execution_node.id` in the READY+schedulable pool). When **true** (default), all **READY** + **`schedulable=true`** nodes in the provider pool are candidates (subject to capacity, slots, heartbeat gate, etc.). |

Pydantic also accepts `devnest_enable_multi_node_scheduling`. Truthy strings: `1`, `true`, `yes`, `on` (case-insensitive).

---

## 2. Primary node when the flag is off

Among rows that are **READY**, **`schedulable=true`**, and pass **`DEVNEST_NODE_PROVIDER`** (`local` / `ec2` / `all`), the **primary** node is the one with the **lowest `execution_node.id`**.

- **Fleet node 2** (or any additional node) is **never** chosen for **new** placements while the flag is **false**, even if an operator temporarily sets **`schedulable=true`** on node 2.
- **Secondary nodes** (e.g. a second EC2 host) participate whenever they are **READY** + **`schedulable=true`** and pass capacity / heartbeat / provider filters. To keep a node catalog-only, set **`schedulable=false`** (see Step 4 runbook).

**Ordering note:** Primary is tied to **database insert order** (lowest id), not to `node_key` names. In typical Phase 3b rollouts, node 1 is registered first, so its id is lower than node 2’s.

---

## 3. Behavior summary

### Flag **off** (opt-in / rollback)

- Placement pool = **at most one** execution node (the primary by min `id`).
- Autoscaler helpers that count “READY+schedulable EC2” for placement-aligned decisions use the **same** pool (so scale-up / idle detection does not assume capacity on nodes that scheduling will never use).
- **No** Traefik or route-admin changes.
- Existing workspaces already pinned to any node are unchanged; this gate applies to **new** selection via `select_node_for_workspace` / `reserve_node_for_workspace`.

### Flag **on** (default)

- Placement pool = all qualifying nodes (effective CPU, memory, active workload spread, `node_key` tiebreak).
- Nodes participate only if **READY** and **`schedulable=true`** (and other existing gates).

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

1. **Default / flag on**  
   - With two **READY** + **`schedulable=true`** nodes, create several workspaces; placements should follow capacity + spread (see [Step 11](./PHASE_3B_STEP11_TWO_NODE_SCHEDULING_SPREAD.md)).  
   - Logs: `scheduler.node.selected` with `placement_single_node_gate=false`, `multi_node_scheduling_enabled=true`.

2. **Secondary node off pool**  
   - Set one node **`schedulable=false`**; new workspaces must not bind to it.

3. **Flag off (rollback test)**  
   - Set `DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=false` on API + worker; restart.  
   - With two **READY** + **`schedulable=true`** nodes, new placements land only on the **primary** (lowest `execution_node.id`).  
   - Logs: `placement_single_node_gate=true`, `multi_node_scheduling_enabled=false`.

4. **Regression**  
   - Smoke: create / open / save / download on workspaces that landed on different nodes when applicable.

5. **Unit tests**  
   - `pytest backend/tests/unit/placement_service/test_node_placement.py backend/tests/unit/autoscaler/ -q`

---

## 6. Rollback

- Set `DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=false`.  
- Restart API and workspace job worker.  
- No database migration; no ingress rollback.

---

## 7. Fleet enablement order (recommended)

1. Register nodes, heartbeat healthy, **read-only smoke** OK (Step 6).  
2. Use **`schedulable=false`** on nodes that should not receive new placements yet.  
3. When ready for multi-node spread, ensure **`DEVNEST_ENABLE_MULTI_NODE_SCHEDULING`** is **true** (default) and set **`schedulable=true`** on nodes that should admit workloads.
