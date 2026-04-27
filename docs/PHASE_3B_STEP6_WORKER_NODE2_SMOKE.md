# Phase 3b Step 6 — Control-plane read-only smoke to node 2 (no placement)

**Goal:** Prove the **control plane** (API process using the same AWS credentials / SSH keys as other infrastructure calls) can resolve **node-2** from `execution_node`, run a **fixed** read-only Docker command on the instance (`docker info` or `docker ps`), and return a **sanitized** result — while **node-2 stays `schedulable=false`** and **no Traefik / routing** changes occur.

**Prerequisites:** [Step 4 — Catalog registration](./PHASE_3B_STEP4_CATALOG_REGISTRATION_NODE2.md) (node-2 row exists, `schedulable=false`), [Step 5 — Heartbeat](./PHASE_3B_STEP5_HEARTBEAT_NODE2.md) (optional but recommended for liveness). Node row must be **`provider_type=ec2`** with **`execution_mode`** `ssm_docker` or `ssh_docker`, plus **SSM** (`provider_instance_id`, `region`) or **SSH** reachability (`ssh_host` / `hostname` / `private_ip`).

---

## 1. API

| Item | Value |
|--------|--------|
| **Method / path** | `POST /internal/execution-nodes/smoke-read-only` |
| **Auth** | `X-Internal-API-Key` (infrastructure scope), same as other `/internal/execution-nodes/*` routes |
| **Body** | `ExecutionNodeSmokeReadOnlyBody`: **`node_id`** *or* **`node_key`** (required), optional **`read_only_command`**: `docker_info` (default) or `docker_ps` |
| **DB / placement** | **Read-only** for scheduling: does **not** update `execution_node` rows, **`schedulable`**, or Traefik. |

**Response** (`ExecutionNodeSmokeResponse`): `ok`, `execution_node_id`, `node_key`, `execution_mode`, `schedulable`, `status`, `command_status` (`Success` | `Failed` | `Skipped`), `output_preview` (truncated, control chars stripped, obvious `AKIA…` access key ids redacted), `provider_instance_id` (not a secret; instance id only).

**Errors:** `404` unknown node; `400` unsupported provider/mode (e.g. `local_docker`); `401`/`403` key issues.

---

## 2. Ops commands

**Script (from bastion / laptop):**

```bash
export DEVNEST_API_BASE="https://<control-plane>" INTERNAL_API_KEY="<infra-key>"
./scripts/devnest_ops_nodes.sh smoke '{"node_key":"node-2","read_only_command":"docker_info"}'
```

Defaults with **`jq`**: `NODE_KEY=node-2`, `read_only_command=docker_info` (override with `SMOKE_READ_ONLY_COMMAND=docker_ps`).

**curl:**

```bash
curl -sS -X POST "${DEVNEST_API_BASE%/}/internal/execution-nodes/smoke-read-only" \
  -H "Content-Type: application/json" \
  -H "X-Internal-API-Key: ${INTERNAL_API_KEY}" \
  -d '{"node_key":"node-2","read_only_command":"docker_ps"}' | jq .
```

**By primary key** (`execution_node.id` from DB or list API):

```bash
curl -sS -X POST "${BASE}/internal/execution-nodes/smoke-read-only" \
  -H "Content-Type: application/json" \
  -H "X-Internal-API-Key: ${INTERNAL_API_KEY}" \
  -d '{"node_id": 2}' | jq .
```

---

## 3. Verification

1. **Smoke succeeds or explains skip/failure:** HTTP `200` with `ok: true` and `command_status: Success`, or `ok: false` with `Skipped`/`Failed` and a short `output_preview` (missing instance id, SSM denied, SSH host missing, etc.).
2. **Schedulable unchanged:**

   ```sql
   SELECT node_key, schedulable FROM execution_node WHERE node_key = 'node-2';
   ```

   Expect **`schedulable = false`** before and after.

3. **Placement unchanged:** Create a test workspace; confirm runtime **`node_id`** is still **node-1** (or another schedulable node), not node-2 — same queries as [Step 5 §3.4](./PHASE_3B_STEP5_HEARTBEAT_NODE2.md).

**Automated unit checks:**

```bash
cd backend && python3 -m pytest \
  tests/unit/infrastructure/test_internal_execution_nodes_routes.py::test_post_smoke_read_only_node2_catalog_keeps_schedulable_false \
  tests/unit/infrastructure/test_internal_execution_nodes_routes.py::test_post_smoke_read_only_by_node_id \
  tests/unit/infrastructure/test_execution_node_smoke.py -q
```

---

## 4. Troubleshooting

| Symptom | Check |
|---------|--------|
| **400** `provider_type=ec2` | Catalog row must be EC2; local dev node cannot use this smoke. |
| **400** `execution_mode` | Use `ssm_docker` or `ssh_docker` on the row. |
| **Skipped** / missing fields | SSM: `provider_instance_id`, `region`. SSH: resolvable host (`ssh_host`, `hostname`, or `private_ip`). |
| **Failed** SSM | IAM for control plane: `ssm:SendCommand`, instance profile + SSM agent on instance, VPC endpoints or egress as per [Step 2](./PHASE_3B_STEP2_SECURITY_GROUPS_NETWORKING.md). |
| **Failed** SSH | Keys on API/worker host, SG **inbound** 22 from control plane, `ssh_user` (default `ubuntu`). |
| **422** on body | Send `node_key` or `node_id`; `read_only_command` must be exactly `docker_info` or `docker_ps`. |

---

## 5. Rollback

- **No migrations or persistent flags** are set by smoke. **Rollback:** stop invoking the endpoint; no SQL undo.
- If you temporarily changed node-2 for debugging (e.g. set `schedulable=true`), revert with catalog/ops tools or SQL per [Step 4](./PHASE_3B_STEP4_CATALOG_REGISTRATION_NODE2.md).

---

## 6. Implementation map

| Area | Location |
|------|-----------|
| Smoke logic | `backend/app/services/infrastructure_service/execution_node_smoke.py` |
| HTTP route | `POST …/smoke-read-only` in `internal_execution_nodes.py` |
| Request/response models | `ExecutionNodeSmokeReadOnlyBody`, `ExecutionNodeSmokeResponse` in `api/schemas.py` |
| Ops script | `scripts/devnest_ops_nodes.sh` **`smoke`** |

---

*Phase 3b Step 6 — read-only execution smoke to node 2 without scheduling.*
