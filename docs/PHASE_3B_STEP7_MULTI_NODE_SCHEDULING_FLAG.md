# Phase 3b Step 7 — Controlled multi-node scheduling enablement

Operator and engineering reference for **`DEVNEST_ENABLE_MULTI_NODE_SCHEDULING`**. No route-admin or Traefik changes; this step only defines **control-plane placement pool** behavior.

**Related:** [Phase 3b fleet runbook](./PHASE_3B_FLEET_RUNBOOK.md), [Step 11 — Two-node spread](./PHASE_3B_STEP11_TWO_NODE_SCHEDULING_SPREAD.md), [Step 5 — Node 2 heartbeat](./PHASE_3B_STEP5_HEARTBEAT_NODE2.md), [Step 6 — Read-only smoke](./PHASE_3B_STEP6_WORKER_NODE2_SMOKE.md).

---

## 1. Environment variable

| Variable | Default | Meaning |
|----------|---------|---------|
| `DEVNEST_ENABLE_MULTI_NODE_SCHEDULING` | **`false`** (Step 7) | When **false**, new workspace placement considers **only the primary execution node** (lowest `execution_node.id` among READY+schedulable after the `DEVNEST_NODE_PROVIDER` filter). When **true**, all **READY** + **`schedulable=true`** nodes in that pool are candidates (subject to capacity, slots, optional **fresh heartbeat** when `DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT=true`). |

Pydantic also accepts `devnest_enable_multi_node_scheduling`. Truthy strings: `1`, `true`, `yes`, `on` (case-insensitive). Falsy: `0`, `false`, `no`, `off`, empty.

---

## 2. Primary node when the flag is off

Among rows that are **READY**, **`schedulable=true`**, and pass **`DEVNEST_NODE_PROVIDER`** (`local` / `ec2` / `all`), the **primary** node is the one with the **lowest `execution_node.id`**.

- With the flag **false** (default), **only** that primary row is eligible for **new** placements. A secondary node (e.g. node 2) with **`schedulable=true`** but a **higher `id`** is **not** chosen until the flag is **true** (or pinned operator CREATE from Step 8, which is separate).
- **`schedulable=false`** on node 2 keeps it out of the pool entirely (whether the flag is on or off). Step 7 assumes node 2 stays **`schedulable=false`** until you deliberately enable it for fleet spread.

**Ordering note:** Primary is tied to **database insert order** (lowest id), not to `node_key` names. In typical Phase 3b rollouts, node 1 is registered first, so its id is lower than node 2’s.

---

## 3. Behavior summary

### Flag **off** (default / safe baseline)

- Placement pool = **at most one** execution node (the primary by min `id` in the filtered READY+schedulable set).
- Autoscaler helpers that count “READY+schedulable EC2” for placement-aligned decisions use the **same** pool (so scale-up / idle detection does not assume capacity on nodes that scheduling will never use).
- **No** Traefik or route-admin changes.
- Existing workspaces already pinned to any node are unchanged; this gate applies to **new** selection via `select_node_for_workspace` / `reserve_node_for_workspace`.

### Flag **on** (explicit fleet enablement)

- Placement pool = all qualifying nodes; the scheduler ranks by effective free CPU/memory, active workload count (spread), then `node_key` tiebreak.
- A node is a candidate only if **READY**, **`schedulable=true`**, and passes capacity, slot ceiling, provider filter, and optional heartbeat freshness.

---

## 4. Placement logging

Structured logs use `log_event` with stable `devnest_event` names.

| Event | Fields |
|-------|--------|
| **`scheduler.node.selected`** | `workspace_id`, `execution_node_id`, `node_key`, `requested_*`, **`multi_node_scheduling_enabled`**, **`placement_single_node_gate`**, **`placement_reason`** (short string: primary-only vs multi-node ranking policy). |
| **`placement.no_schedulable_node`** | Same telemetry as above, plus `detail` (truncated, no secrets). |
| **`placement.decision.summary`** | `placement_summary` — flattened digest from `explain_placement_decision` (pool size, effective free resources, sort policy). |

`NoSchedulableNodeError` messages append a pointer to this doc when the single-node gate is on.

---

## 5. Verification checklist

| Scenario | Expect |
|----------|--------|
| **Flag off** (unset or `false`) + node-1 and node-2 both READY+schedulable | New workspaces land on **primary only** (lowest `execution_node.id`, typically node-1). Logs: `placement_single_node_gate=true`, `multi_node_scheduling_enabled=false`, `placement_reason` contains `primary_only`. |
| **Flag on** + node-2 **`schedulable=false`** | Node 2 is **not** in the pool; placements stay on schedulable nodes (e.g. node-1). |
| **Flag on** + node-2 **`schedulable=true`** + heartbeat/capacity OK | Node 2 **can** be selected when it ranks best under the sort policy (not automatic every time). |
| **Regression** | Create / open / save / download on a workspace on node-1 still works. |

**Commands:**

```bash
cd backend && python3 -m pytest \
  tests/unit/placement_service/test_node_placement.py \
  tests/unit/scheduler/test_scheduler_service.py -q
```

---

## 6. Rollback

- **From multi-node back to primary-only:** set `DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=false` (or unset), restart **API** and **workspace job worker** (any process that calls `schedule_workspace` / `reserve_node_for_workspace`).
- **Baseline (Step 7 default):** flag is already **false**; no action required for “only node-1” behavior.
- No database migration; no ingress rollback.

---

## 7. Fleet enablement order (recommended)

1. Register nodes, heartbeat healthy, read-only smoke OK (Steps 4–6).  
2. Keep **`schedulable=false`** on node 2 until you are ready for it to compete for placements.  
3. Set **`DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=true`** on API + worker; restart.  
4. Set **`schedulable=true`** (and **READY**) on node 2 when you want it to receive new workspaces under the multi-node policy.

There is **no** automatic placement on node 2 unless **both** the flag is **true** and the row is **READY** + **`schedulable=true`** (and passes capacity / heartbeat gates).
