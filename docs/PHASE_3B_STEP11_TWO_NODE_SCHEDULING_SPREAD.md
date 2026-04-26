# Phase 3b Step 11 — Generic two-node scheduling and spread

**Goal:** Normal scheduler behavior across every **READY** + **`schedulable=true`** node in the provider pool (no hardcoded node ids). Capacity-first ordering, then **active workload count** (spread), then **`node_key`** tiebreak.

**Related:** [Step 7 — Multi-node flag](./PHASE_3B_STEP7_MULTI_NODE_SCHEDULING_FLAG.md) (now **defaults to multi-node on**), [WORKSPACE_NODES.md](./WORKSPACE_NODES.md), [Step 8 — Optional pinned operator CREATE](./PHASE_3B_STEP8_CONTROLLED_NODE2_TEST_WORKSPACE.md) (default **off**; not required for fleet spread).

---

## 1. Scheduling behavior (summary)

| Input | Behavior |
|-------|----------|
| Several **READY** + **`schedulable=true`** nodes, matching `DEVNEST_NODE_PROVIDER`, optional fresh heartbeat gate | `select_node_for_workspace` scores by **effective free CPU** (desc), **effective free memory** (desc), **active workload count** (asc — spread), **`node_key`** (asc). |
| One secondary node **`schedulable=false`** or not **READY** | It is **not** in the pool; new workspaces use remaining nodes only (e.g. node 1 only if node 2 is drained). |
| All nodes lack capacity or slots | **`NoSchedulableNodeError`** → HTTP **503** with a short user-facing message from workspace create (see `WorkspaceSchedulingCapacityError`). |
| Operator pinned CREATE (Step 8) | Only when **`DEVNEST_ALLOW_PINNED_CREATE_PLACEMENT=true`** and allowlisted ids; **not** used for normal user creates. |

**No code path should assume a literal `node-2` id** — use **`execution_node`** rows, **`node_key`**, and **`schedulable` / `status`** only.

---

## 2. Verification (manual / staging)

Prerequisites: API + worker share DB; both execution nodes **READY**, **`schedulable=true`**, heartbeats fresh if **`DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT=true`**.

1. **Baseline pool**  
   `GET /internal/execution-nodes/` (infrastructure key) — confirm two rows with `schedulable: true`, distinct `node_key`, and non-zero `available_workspace_slots`.

2. **Spread / distribution**  
   Create several workspaces via public **`POST /workspaces`** (authenticated). After jobs complete, inspect `workspace_runtime.node_id` (or `workspace.execution_node_id`) — you should see **both** `node_key` values represented over multiple creates (exact order depends on capacity and load).

3. **No overbooking**  
   Fill **`max_workspaces`** on one node (or exhaust effective CPU) — next placement should prefer the other node; if **both** are exhausted, create returns **503** with the capacity message.

4. **Drain node 2**  
   `POST /internal/execution-nodes/drain` with node 2’s key (or set `schedulable=false` / status off **READY** per your ops model). New creates should land only on remaining schedulable nodes (e.g. node 1).

5. **Fill node 1, free node 2**  
   With node 2 schedulable again and node 1 at slot/cpu limit, new workspaces should use **node 2** when it is the only qualifying node (or has more headroom).

6. **Snapshots**  
   Save/download on workspaces pinned to **each** node (placement-driven); object storage or local staging should follow existing Step 10 behavior per node.

---

## 3. Example commands

Replace `API`, `TOKEN`, `INTERNAL_KEY`, and `NODE_KEY` with your environment values.

```bash
# Pool (internal)
curl -sS -H "X-Internal-API-Key: ${INTERNAL_KEY}" "${API}/internal/execution-nodes/" | jq .

# Create workspaces (user JWT)
for i in 1 2 3 4; do
  curl -sS -X POST "${API}/workspaces" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"spread-test-$i\",\"description\":\"step11\",\"is_private\":true}" | jq .
done

# Placement audit (Postgres) — adjust DSN
psql "${DATABASE_URL}" -c "
  SELECT w.workspace_id, w.name, w.execution_node_id, en.node_key, wr.node_id
  FROM workspace w
  JOIN execution_node en ON en.id = w.execution_node_id
  LEFT JOIN workspace_runtime wr ON wr.workspace_id = w.workspace_id
  WHERE w.name LIKE 'spread-test-%'
  ORDER BY w.workspace_id;
"
```

Optional: **force primary-only** for a rollback test — set `DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=false` on API + worker, restart, repeat a create; only **min(`execution_node.id`)** among READY+schedulable should receive new placements.

---

## 4. Rollback

1. Set **`DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=false`** on **API** and **workspace job worker** (and any other process that calls `schedule_workspace` / `reserve_node_for_workspace`).  
2. Restart those processes.  
3. No DB migration. Existing workspace pins are unchanged.  
4. Keep optional Step 8 pinned routes **disabled** (`DEVNEST_ALLOW_PINNED_CREATE_PLACEMENT=false`) unless you explicitly need pinned diagnostics.

---

## 5. Automated regression

```bash
cd backend && pytest tests/unit/placement_service/test_node_placement.py \
  tests/unit/placement_service/test_orchestrator_binding.py \
  tests/unit/scheduler/test_scheduler_service.py -q
```
