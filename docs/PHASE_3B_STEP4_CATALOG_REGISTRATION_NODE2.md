# Phase 3b Step 4 — Catalog-only registration for node 2 (documentation only)

**Status:** Operator runbook. **No** application code changes in this step, **no** route-admin or Traefik changes, **no** requirement to exercise remote Docker/SSM for placements.

**Prerequisites:** [Step 1](./PHASE_3B_STEP1_EXECUTION_NODE_EC2.md), [Step 2](./PHASE_3B_STEP2_SECURITY_GROUPS_NETWORKING.md), [Step 3](./PHASE_3B_STEP3_IAM_EXECUTION_NODES.md), [Fleet runbook](./PHASE_3B_FLEET_RUNBOOK.md).

**Goal:** Insert **node 2** into the `execution_node` catalog with correct EC2 metadata and capacity fields, with **`schedulable = false`** so the **scheduler never selects it** for new workspaces, while **node 1** behavior stays unchanged.

---

## 1. Scheduler behavior (why `schedulable=false` is enough)

Placement uses **`ExecutionNode.schedulable == True`** and **`ExecutionNode.status == ExecutionNodeStatus.READY`** (see `backend/app/services/placement_service/node_placement.py`, `_schedulable_base_predicates`).

Therefore:

- **`schedulable=false`** → node is **excluded** from placement, regardless of other fields (as long as status is not the sole gate in a way that still excludes — both must hold for selection, so **false schedulable is sufficient**).
- **`status=READY`** + **`schedulable=false`** is a valid **catalog-only** posture: row looks “healthy” for operators but **cannot** receive workloads.

**No scheduler code change** is required for Step 4: the existing predicate already ignores non-schedulable nodes.

---

## 2. Important: default `register-existing` behavior when EC2 is **running**

`POST /internal/execution-nodes/register-existing` (and `scripts/register_ec2_instance.py`, which calls the same `register_ec2_instance` logic) **hydrates** the row from EC2 `describe-instances`.

For a **new** row when the instance state is **`running`**, `compute_status_schedulable_after_ec2_sync` returns **`READY` + `schedulable=true`** (`backend/app/services/providers/ec2_provider.py`).

So **a single `register-existing` call on a running node 2 would make it schedulable** unless you follow §3.

---

## 3. Catalog-only registration flow (recommended sequence)

### 3.1 Preconditions

- [ ] EC2 for node 2 exists (Step 1) with SG/IAM (Steps 2–3).
- [ ] Choose a **unique** `node_key` (e.g. `node-2`) — must not collide with node 1.
- [ ] Control plane can call **EC2 DescribeInstances** for that instance (API credentials / role on server).
- [ ] Internal API key for **infrastructure** scope available to the operator (store in secrets manager; **do not** commit).

### 3.2 Step A — Register from EC2 (populate catalog fields)

Use **either**:

**A. Internal HTTP API**

```http
POST /internal/execution-nodes/register-existing
X-Internal-API-Key: <INFRASTRUCTURE_SCOPED_KEY>
Content-Type: application/json

{
  "instance_id": "<INSTANCE_ID>",
  "node_key": "node-2",
  "execution_mode": "ssm_docker",
  "ssh_user": "ubuntu"
}
```

**B. CLI script** (from `backend/` with `DATABASE_URL` and AWS credentials configured):

```bash
PYTHONPATH=. python scripts/register_ec2_instance.py <INSTANCE_ID> --node-key node-2 --execution-mode ssm_docker
```

The script prints **`status`** and **`schedulable`** at the end — **read them**.

### 3.3 Step B — Force **catalog-only**: set `schedulable = false`

If Step A printed **`schedulable=True`** (typical when the instance is **running**), you **must** apply Step B **before** any traffic validation that could create workspaces.

**Option 1 — SQL (explicit, common for one-shot ops)**

Run against the application database (replace schema if not `public`):

```sql
BEGIN;
SELECT id, node_key, status, schedulable
FROM execution_node
WHERE node_key = 'node-2'
FOR UPDATE;

UPDATE execution_node
SET schedulable = false,
    updated_at = NOW()
WHERE node_key = 'node-2';

COMMIT;
```

**Option 2 — Internal drain API (semantic warning)**

`POST /internal/execution-nodes/drain` sets **`DRAINING`** + **`schedulable=false`**. That **does** exclude the node from placement but mislabels lifecycle for a “warm catalog” row. Prefer **Option 1** unless you intentionally want **DRAINING** visible.

**Do not** use **`deregister`** for catalog-only lock — it sets **`TERMINATED`** and is meant for removal semantics.

### 3.4 Optional catalog fields checklist (after register + lock)

Confirm the row matches EC2 (either via `GET /internal/execution-nodes/` or SQL):

| Field | Expected |
|--------|----------|
| `node_key` | e.g. `node-2` |
| `name` | Name tag or default from registration |
| `provider_type` | `ec2` |
| `provider_instance_id` | `i-…` |
| `region` | AWS region |
| `private_ip` | Matches describe |
| `public_ip` | If applicable |
| `execution_mode` | `ssm_docker` or `ssh_docker` (matches future worker path) |
| Capacity (`total_*`, `allocatable_*`, `max_workspaces`, …) | Filled from instance type defaults / describe |
| `status` | Often `READY` after register when running — **allowed** with `schedulable=false` |
| **`schedulable`** | **`false`** after Step B |

### 3.5 Heartbeat (optional in Step 4)

You may **`POST /internal/execution-nodes/heartbeat`** for `node-2` to validate API + network paths. Heartbeat **does not** set `schedulable` to **true** by itself (see `internal_execution_nodes.py`). Placement still requires **`schedulable=true`**.

---

## 4. Validation commands

No secrets in examples; use your secure key injection.

### 4.1 List execution nodes (API)

```bash
curl -sS -H "X-Internal-API-Key: <KEY>" "https://<API_HOST>/internal/execution-nodes/" | jq .
```

**Expect:** Two rows (node 1 + node 2) or your full fleet; **node-2** present.

### 4.2 Confirm node 2 exists and `schedulable=false` (SQL)

```sql
SELECT node_key, status, schedulable, provider_instance_id, private_ip, execution_mode
FROM execution_node
WHERE node_key = 'node-2';
```

**Expect:** One row; **`schedulable` = false** (or `f` in psql).

### 4.3 Confirm new workspace still lands on node 1

**Approach A — Create a test workspace** in a non-production environment and inspect placement result:

- Check DB: `workspace` / `workspace_runtime` (or placement logs) for **`execution_node_id`** or **`node_id`** / `node_key` matching **node 1** only.

**Approach B — SQL after create** (schema names may vary):

```sql
SELECT w.workspace_id, w.execution_node_id, en.node_key
FROM workspace w
LEFT JOIN execution_node en ON en.id = w.execution_node_id
ORDER BY w.workspace_id DESC
LIMIT 5;
```

**Expect:** New workspace’s **`node_key`** (or FK) is **not** `node-2` while `node-2.schedulable` is false.

### 4.4 Placement predicate sanity (read-only code reference)

Grep locally (developers):

```bash
rg "schedulable == True" backend/app/services/placement_service/node_placement.py
```

**Expect:** `_schedulable_base_predicates` requires **`ExecutionNode.schedulable == True`**.

---

## 5. Rollback steps

| Step | Action |
|------|--------|
| 1 | Ensure **no** workspace rows reference node 2 (`execution_node_id` / runtime `node_key`). If any test bind exists, move or delete that workspace per product procedures. |
| 2 | Call **`POST /internal/execution-nodes/deregister`** with `node_key: "node-2"` (infrastructure key). |
| 3 | Optionally **remove** the catalog row only if product policy allows hard delete — today’s API favors **soft** `TERMINATED` via deregister; prefer **deregister** over ad-hoc `DELETE` unless DBAs agree. |
| 4 | **Terminate** EC2 instance only if you are discarding the host (separate AWS action). |

If Step B used **SQL only** and you want to undo before any workload:

```sql
UPDATE execution_node SET schedulable = true WHERE node_key = 'node-2';
```

**Only** do this when you intentionally want node 2 to become schedulable (not a rollback for “remove node 2” — for removal use **deregister**).

---

## 6. Definition of done (Step 4 only)

- [ ] Node 2 row exists in **`execution_node`** with correct **EC2-linked** fields.  
- [ ] **`schedulable = false`** on node 2 after the catalog-only lock (§3.3).  
- [ ] **`GET /internal/execution-nodes/`** shows node 2 with **`schedulable: false`**.  
- [ ] Creating a **new** workspace still results in placement on **node 1** (or other schedulable nodes), **not** node 2.  
- [ ] No route-admin / Traefik / worker execution-path changes were required for this step.  
- [ ] Rollback path in **§5** is understood by operators.

---

**Next:** [Phase 3b Step 5 — Heartbeat from node 2](./PHASE_3B_STEP5_HEARTBEAT_NODE2.md) (liveness while `schedulable=false`).

---

## 7. Follow-up (not Step 4)

- Add a first-class **`register-catalog`** or **`PATCH`** internal field for `schedulable` without SQL (future PR) to avoid the **register-then-SQL** gap documented in §2–§3.  
- When ready for workloads: set **`schedulable=true`** (and ensure **READY** + capacity + heartbeat policy) in a **later** gated step.

---

## 8. Files touched by this step

| File | Role |
|------|------|
| `docs/PHASE_3B_STEP4_CATALOG_REGISTRATION_NODE2.md` | This Step 4 catalog registration runbook. |

---

*Phase 3b Step 4 — catalog-only registration. Documentation only.*
