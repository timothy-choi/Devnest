# Phase 3b Step 8 — One controlled test workspace on node 2

This document describes the **safest** way to run **exactly one** (or a small number of) operator-controlled test workspace(s) on **execution node 2** without changing normal user placement on node 1.

**Scope:** explicit env flags + **internal HTTP API** + optional allowlist of `execution_node.id`. Normal `POST /workspaces` behavior is unchanged.

**Related:** [Fleet runbook](./PHASE_3B_FLEET_RUNBOOK.md), [Step 7 — Multi-node scheduling flag](./PHASE_3B_STEP7_MULTI_NODE_SCHEDULING_FLAG.md), [Step 6 — Read-only smoke to node 2](./PHASE_3B_STEP6_WORKER_NODE2_SMOKE.md) (`POST /internal/execution-nodes/smoke-read-only`).

---

## 1. Implementation plan (summary)

| Layer | Behavior |
|--------|-----------|
| **Safety gates** | `DEVNEST_ALLOW_PINNED_CREATE_PLACEMENT=false` by default. When false, the pinned path is **inactive**. `DEVNEST_PINNED_CREATE_EXECUTION_NODE_IDS` lists the only registry PKs allowed for pinned CREATE (comma-separated integers). |
| **Internal API** | `POST /internal/test-workspaces/pinned-operator-create` with `X-Internal-API-Key` (**same scope as** `/internal/execution-nodes/*`, i.e. `InternalApiScope.INFRASTRUCTURE`). Body: `owner_user_id`, `execution_node_id`, optional `description` / `runtime`. |
| **Workspace row** | Server assigns name prefix `devnest-op-pinned-test-…` and sets `Workspace.execution_node_id` **before** queuing CREATE. |
| **CREATE placement** | `resolve_orchestrator_placement` detects pinned workspaces (flag + allowlist + name prefix) and **skips the scheduler**, using the pinned node’s `node_key` and topology (same rules as normal placement for topology: node `default_topology_id` or env fallback). |
| **Worker / Docker** | Existing CREATE job path: orchestrator creates project dir and starts the container on the resolved **node 2** (SSM/SSH/local as configured on that `execution_node`). |
| **Traefik** | Unchanged product logic: when `DEVNEST_GATEWAY_ENABLED=true`, the worker registers the route after bring-up to `public_host` → route-admin → Traefik (same as any workspace reaching `RUNNING`). |
| **Node 1** | Default scheduling and user-created workspaces are unaffected; they do not use the pinned name prefix or internal route. |

---

## 2. Prerequisites (operator)

1. **Node 2** registered, `READY`, **`schedulable=true`** for the duration of the test (pinned CREATE validates this).
2. **Topology:** target `ExecutionNode.default_topology_id` set (recommended) or catalog topology `1` available (dev default), matching strict-placement rules if enabled.
3. **Worker** can reach node 2 (SSM/SSH smoke already succeeded in Step 6).
4. **Traefik / route-admin:** `DEVNEST_GATEWAY_ENABLED=true`, `DEVNEST_GATEWAY_URL` reachable from worker, Traefik SG can reach node 2 workspace ports (see Step 2 runbook).
5. **Owner user:** a real `user_auth_id` (or dedicated operator test user) for `owner_user_id` — quotas still apply.
6. Look up **`execution_node.id`** for node 2 (SQL or internal list API).

---

## 3. Enablement (narrow window)

Set on **API** and **workspace job worker** (and any in-process worker):

```bash
export DEVNEST_ALLOW_PINNED_CREATE_PLACEMENT=true
export DEVNEST_PINNED_CREATE_EXECUTION_NODE_IDS=<target_execution_node_id>
# Optional: primary-node-only pool for normal creates (rollback / Step 7 off)
# export DEVNEST_ENABLE_MULTI_NODE_SCHEDULING=false
```

Restart processes after changing env.

---

## 4. Create the test workspace (internal API)

```bash
curl -sS -X POST "https://<API_HOST>/internal/test-workspaces/pinned-operator-create" \
  -H "Content-Type: application/json" \
  -H "X-Internal-API-Key: <INFRASTRUCTURE_INTERNAL_API_KEY>" \
  -d "{\"owner_user_id\": <USER_AUTH_ID>, \"execution_node_id\": <NODE_2_EXECUTION_NODE_ID>}"
```

Response **202** with `workspace_id`, `job_id`. Process the job with your normal worker (`POST /internal/workspace-jobs/process` or poll loop).

---

## 5. Verification

1. **DB:** `workspace.name` starts with `devnest-op-pinned-test-`; `execution_node_id` equals node 2’s id.
2. **After RUNNING:** `workspace_runtime.node_id` is node 2’s `node_key`.
3. **Node 2 disk:** project directory exists under your DevNest workspace root layout (same as node 1 bring-up).
4. **Docker:** container running on node 2 (SSM/SSH `docker ps` or node agent).
5. **Traefik:** with gateway enabled, route registered; open IDE URL with `Host: <workspace.public_host>` (or browser URL your product uses).
6. **Other nodes:** with normal **user** `POST /workspaces` (no pinned prefix), placement follows the **scheduler** (multi-node by default since Step 11); it is **not** forced onto the pinned node unless you use the internal pinned route.

**Automated (CI):**

```bash
cd backend && python3 -m pytest \
  tests/unit/infrastructure/test_internal_pinned_operator_create.py \
  tests/unit/placement_service/test_orchestrator_binding.py::test_create_operator_pinned_skips_scheduler \
  -q
```

---

## 6. Rollback

1. **Stop/delete the test workspace** (normal stop/delete intents) so the container exits and route-admin can deregister (best-effort deregister on stop/delete paths).
2. Set **`DEVNEST_ALLOW_PINNED_CREATE_PLACEMENT=false`** (or unset) and **clear** `DEVNEST_PINNED_CREATE_EXECUTION_NODE_IDS`; restart API + worker.
3. Optionally set the target node **`schedulable=false`** again; disable pinned placement env flags when finished.
4. **No DB migration rollback** for this feature; workspace rows remain historical unless deleted.

---

## 7. Files touched (engineering reference)

| Path | Role |
|------|------|
| `backend/app/libs/common/config.py` | `devnest_allow_pinned_create_placement`, `devnest_pinned_create_execution_node_ids` |
| `backend/app/services/placement_service/operator_pinned_create.py` | Name prefix + allowlist parsing + `workspace_uses_operator_pinned_create` |
| `backend/app/services/placement_service/orchestrator_binding.py` | CREATE branch: pinned placement vs scheduler |
| `backend/app/services/workspace_service/errors.py` | Pinned operator error types |
| `backend/app/services/workspace_service/services/workspace_intent_service.py` | `create_operator_pinned_test_workspace` |
| `backend/app/services/workspace_service/api/routers/internal_operator_test_workspaces.py` | Internal route |
| `backend/app/services/workspace_service/api/routers/__init__.py`, `backend/app/main.py` | Router registration |
| `backend/tests/unit/infrastructure/test_internal_pinned_operator_create.py` | API + DB tests |
| `backend/tests/unit/placement_service/test_orchestrator_binding.py` | CREATE pinned placement unit test |
| `backend/tests/unit/infrastructure/conftest.py` | Topology metadata for SQLite |

---

## 8. Threat model (short)

- Without **both** the env flag and **allowlisted id**, pinned placement **never** runs.
- The **`devnest-op-pinned-test-`** prefix prevents accidental alignment with manually crafted rows unless an attacker can already create workspaces with that name **and** pass quotas — internal API is **infrastructure-key** protected; public API does not expose pinned create.
- Remove allowlist entries immediately after the test window.
