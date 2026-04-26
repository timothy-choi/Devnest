# Phase 3b Step 8 — One controlled test workspace on node 2

Operator path to run **exactly one** (or a few) **internal** test workspace(s) on **node 2** without turning on broad multi-node placement for normal users — **unless** you also satisfy the same gates normal scheduling would use for fleet spread.

**Scope:** internal HTTP API + env flags + allowlisted `execution_node.id`. **Normal `POST /workspaces`** is unchanged and never uses pinned placement. **No migration** of existing workspaces.

**Related:** [Fleet runbook](./PHASE_3B_FLEET_RUNBOOK.md), [Step 7 — Multi-node flag](./PHASE_3B_STEP7_MULTI_NODE_SCHEDULING_FLAG.md), [Step 6 — Read-only smoke](./PHASE_3B_STEP6_WORKER_NODE2_SMOKE.md), [Step 5 — Heartbeat](./PHASE_3B_STEP5_HEARTBEAT_NODE2.md).

---

## 1. Preconditions (all required)

Pinned operator CREATE is accepted **only** when:

| Gate | Requirement |
|------|-------------|
| **Internal feature** | `DEVNEST_ALLOW_PINNED_CREATE_PLACEMENT=true` |
| **Allowlist** | Target `execution_node.id` ∈ `DEVNEST_PINNED_CREATE_EXECUTION_NODE_IDS` (comma-separated) |
| **Multi-node flag** | **`DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=true`** on API + worker (Step 7). If **false**, pinned CREATE returns **400** — prevents a node-2 exception while the fleet stays on primary-only scheduling. |
| **Node row** | Target node **READY**, **`schedulable=true`**, non-empty **`node_key`**, **`default_topology_id`** when strict placement is enforced |
| **Heartbeat** | **`last_heartbeat_at`** non-null and within **`DEVNEST_NODE_HEARTBEAT_MAX_AGE_SECONDS`** (default 300). Heartbeat is **always** checked for pinned CREATE (independent of `DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT`, which only affects normal scheduler placement). |

CREATE job processing uses the **same** orchestrator / node execution path as normal workspaces (project dir, container start, `WorkspaceRuntime` with `node_id` / topology). With **`DEVNEST_GATEWAY_ENABLED=true`**, route-admin / Traefik register the workspace host to the upstream on node 2 like any RUNNING workspace.

---

## 2. Implementation summary

| Layer | Behavior |
|--------|-----------|
| **Internal API** | `POST /internal/test-workspaces/pinned-operator-create` (`X-Internal-API-Key`, infrastructure scope). Body: `owner_user_id`, `execution_node_id`, optional `description` / `runtime`. |
| **Workspace row** | Server assigns name prefix `devnest-op-pinned-test-…` and sets `Workspace.execution_node_id` before queuing CREATE. |
| **Placement** | `resolve_orchestrator_placement` uses pinned path (no scheduler ranking). Re-validates multi-node + heartbeat when the worker resolves placement. |
| **Runtime** | Worker persists **`WorkspaceRuntime`** with **`node_id`** = target **`node_key`** and topology after bring-up (same as standard CREATE). |
| **Node 1** | User-created workspaces still use the scheduler; with Step 7 default **`DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=false`**, they stay on **primary** (typically node 1) unless you enable multi-node for the whole fleet. |

---

## 3. Enablement (narrow window)

Set on **API** and **workspace job worker**:

```bash
export DEVNEST_ALLOW_PINNED_CREATE_PLACEMENT=true
export DEVNEST_PINNED_CREATE_EXECUTION_NODE_IDS=<node_2_execution_node_id>
export DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=true
# Ensure node-2 POSTs heartbeat within max age (Step 5) or worker emits heartbeats.
# export DEVNEST_NODE_HEARTBEAT_MAX_AGE_SECONDS=300
```

Restart processes after changing env.

---

## 4. Create the test workspace

```bash
curl -sS -X POST "https://<API_HOST>/internal/test-workspaces/pinned-operator-create" \
  -H "Content-Type: application/json" \
  -H "X-Internal-API-Key: <INFRASTRUCTURE_INTERNAL_API_KEY>" \
  -d "{\"owner_user_id\": <USER_AUTH_ID>, \"execution_node_id\": <NODE_2_EXECUTION_NODE_ID>}"
```

Expect **202** with `workspace_id`, `job_id`. Process the CREATE job with your normal worker.

---

## 5. Verification

1. **Normal workspace:** create via public **`POST /workspaces`** (no pinned prefix) — with multi-node **false** (default), it should land on **node 1** (primary). With multi-node **true** and both nodes schedulable, behavior follows Step 7/11 ranking.
2. **Pinned workspace:** `workspace.name` prefix `devnest-op-pinned-test-`; `execution_node_id` = node 2; after RUNNING, **`workspace_runtime.node_id`** = node 2’s **`node_key`**.
3. **Node 2:** project directory and container exist (same bring-up as node 1).
4. **Gateway:** open IDE URL; save/download succeed.
5. **Automated (CI):**

```bash
cd backend && python3 -m pytest \
  tests/unit/infrastructure/test_internal_pinned_operator_create.py \
  tests/unit/placement_service/test_orchestrator_binding.py::test_create_operator_pinned_skips_scheduler \
  tests/unit/placement_service/test_orchestrator_binding.py::test_create_operator_pinned_rejects_when_multi_node_off \
  -q
```

---

## 6. Rollback

1. **Delete or stop** the test workspace (normal intents).
2. Set **`schedulable=false`** on node 2 when you want it out of any future pool.
3. Set **`DEVNEST_ALLOW_PINNED_CREATE_PLACEMENT=false`** (or unset) and clear **`DEVNEST_PINNED_CREATE_EXECUTION_NODE_IDS`**.
4. Set **`DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=false`** (or unset, Step 7 default) if you enabled multi-node only for this test and want primary-only fleet behavior again.
5. Restart API + worker.

---

## 7. Engineering reference

| Path | Role |
|------|------|
| `backend/app/services/placement_service/node_heartbeat.py` | `execution_node_heartbeat_within_max_age` |
| `backend/app/services/placement_service/operator_pinned_create.py` | `validate_operator_pinned_create_node_gates` |
| `backend/app/services/placement_service/orchestrator_binding.py` | Pinned CREATE placement + gate re-check |
| `backend/app/services/workspace_service/services/workspace_intent_service.py` | `create_operator_pinned_test_workspace` |
| `backend/app/services/workspace_service/api/routers/internal_operator_test_workspaces.py` | HTTP route |

---

*Phase 3b Step 8 — controlled test workspace on node 2.*
