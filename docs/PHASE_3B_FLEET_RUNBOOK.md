# Phase 3b — Multi-EC2 execution fleet runbook (documentation only)

This document is **Step A** of Phase 3b: a **fleet runbook** and **network / IAM matrix** for adding **N** EC2 workspace execution nodes (validated first with a **second** node). It contains **no secrets**, no infrastructure commands that embed credentials, and no requirement to provision a second EC2 instance yet.

**Audience:** operators and engineers extending DevNest from a stable single-node deployment.

**Related product areas:** `execution_node` registry, placement / scheduler, workspace job worker, internal execution-node APIs, route-admin / Traefik, S3-backed snapshots, SSM (or SSH) execution modes.

**Related:**

- [Phase 3b Step 1 — EC2 execution-node template](./PHASE_3B_STEP1_EXECUTION_NODE_EC2.md) — provisioning an additional EC2 host (Docker, SSM, paths, validation). **No registry** in that step.
- [Phase 3b Step 2 — Security groups and networking](./PHASE_3B_STEP2_SECURITY_GROUPS_NETWORKING.md) — SGs, port matrix, Traefik→node path, validation and rollback.
- [Phase 3b Step 3 — IAM for execution nodes](./PHASE_3B_STEP3_IAM_EXECUTION_NODES.md) — instance profile, S3/ECR/SSM/STS policies, example JSON, validation and rollback.
- [Phase 3b Step 4 — Catalog registration (node 2)](./PHASE_3B_STEP4_CATALOG_REGISTRATION_NODE2.md) — `register-existing` + `schedulable=false`, validation, rollback (no routing changes).
- [Phase 3b Step 5 — Heartbeat from node 2](./PHASE_3B_STEP5_HEARTBEAT_NODE2.md) — systemd/cron → `POST /internal/execution-nodes/heartbeat`, stay `schedulable=false`.
- [Phase 3b Step 7 — Multi-node scheduling flag](./PHASE_3B_STEP7_MULTI_NODE_SCHEDULING_FLAG.md) — `DEVNEST_ENABLE_MULTI_NODE_SCHEDULING` (default **false**; set **true** for fleet spread), primary-node gate when **false**, placement logging.
- [Phase 3b Step 11 — Two-node scheduling spread](./PHASE_3B_STEP11_TWO_NODE_SCHEDULING_SPREAD.md) — verification and rollback for generic fleet placement.
- [Phase 3b Step 12 — Ops hardening](./PHASE_3B_STEP12_OPS_HARDENING.md) — drain/undrain/deregister runbooks, internal APIs, diagnostics, rollback.
- [Operator runbooks (index)](./runbooks/README.md) — drain, undrain, deregister, stale heartbeat, disk full, failed workspace.
- [Phase 3b Step 8 — Controlled test workspace on node 2](./PHASE_3B_STEP8_CONTROLLED_NODE2_TEST_WORKSPACE.md) — pinned internal CREATE, Traefik verification, rollback.

---

## 1. Purpose and scope

**Goal:** Before changing Terraform, security groups, application code, or provisioning a second instance, align on:

- How a **fleet** of identical execution nodes fits into DevNest.
- **Who may talk to whom** on the network (security groups).
- **What IAM** execution instances need (instance profile).
- How to **register**, **observe**, **drain**, **deregister**, and **terminate** nodes using existing control-plane concepts.
- **Rollback** and **definition of done** for Phase 3b.

**Out of scope for this file:** concrete AMI IDs, account IDs, bucket names, keys, hostnames, or step-by-step cloud console clicks for a specific AWS account.

---

## 2. Target N-node execution fleet architecture

### 2.1 Roles

| Layer | Responsibility |
|--------|----------------|
| **Control plane** | API + DB: `execution_node` registry, workspace and job lifecycle, placement decisions, internal routes (`/internal/execution-nodes/*`), snapshot metadata, optional autoscaler hooks. Does not run user containers. |
| **Workspace job worker** | Dequeues jobs, orchestrates Docker (or remote Docker via SSH/SSM) **on the execution node bound** to the workspace, persists runtime state, emits internal HTTP calls (e.g. heartbeats toward API when configured). |
| **Execution nodes (N)** | EC2 instances running Docker, workspace bind mounts, and optional local caches. Each node corresponds to one `execution_node` row (`node_key`, capacity, `execution_mode`, provider metadata). |
| **Node heartbeat** | Periodic **liveness + coarse capacity** signal (e.g. HTTP `POST /internal/execution-nodes/heartbeat` with infrastructure-scoped internal auth). Used for ops visibility and optional placement freshness gates. |
| **Scheduler / placement** | Selects a **READY**, **schedulable** node with sufficient **effective** capacity; applies spreading and anti-overbooking rules. |
| **Route-admin + Traefik** | **Central** ingress: browser hits Traefik; dynamic routes map workspace hostnames to the **current** backend (execution node private IP + service ports). |

### 2.2 Data flow (conceptual)

1. Operator or automation **registers** a new EC2 instance → `execution_node` row.
2. **Heartbeat** keeps `last_heartbeat_at` (and optional metadata) fresh.
3. **Scheduler** binds a workspace to a node (stored on workspace / runtime / job as designed in product).
4. **Worker** executes orchestration **on that node** (SSM, SSH, or future node-agent).
5. **Route-admin** updates Traefik so the workspace hostname targets that node’s reachable address.

### 2.3 Design principle

**N identical pool members** — the second EC2 instance is only the **first additional** member of the same pool, not a one-off “node 2” code path.

---

## 3. Security group matrix

Rules below are **directional**. Actual group names, IDs, and CIDRs are account-specific. Prefer **security group references** (source = other SG id) over large CIDRs.

### 3.1 Legend

- **Ingress:** who may connect **to** this tier.
- **Egress:** where this tier may connect **out**.

### 3.2 Matrix (summary)

| Source → Destination | Protocol / ports | Purpose |
|----------------------|-------------------|---------|
| **Internet / users** → **Traefik / gateway** | TCP 443 (and 80 if used) | Browser and API clients to edge. |
| **Traefik / gateway** → **Execution nodes** | TCP (workspace IDE + app ports as defined by product; e.g. high ports) | Proxy user traffic to the node that hosts the workspace. |
| **Traefik / gateway** → **Control plane API** | TCP 443 or internal HTTP | Optional admin or health; keep narrow if used. |
| **Control plane / worker** → **Execution nodes** | **SSM:** no inbound to node from API for default SSM (uses SSM agent outbound). **SSH:** TCP 22 **only** if `execution_mode` uses SSH, from a **defined** bastion or worker SG — avoid open SSH to the world. | Orchestration: Run Command / SSH Docker. |
| **Control plane / worker** → **RDS** | TCP 5432 (or your DB port) | Application and worker DB access. |
| **Control plane / worker** → **S3** | HTTPS 443 | Snapshot upload/download via AWS API (VPC endpoint or public endpoint per design). |
| **Control plane / worker** → **ECR / image registry** | HTTPS 443 | If worker or nodes pull images via registry APIs from this network path. |
| **Execution nodes** → **S3** | HTTPS 443 | Snapshot tooling on node if design pushes bytes from instance role. |
| **Execution nodes** → **ECR** | HTTPS 443 | Pull workspace image if pulls run on node. |
| **Execution nodes** → **SSM / EC2 messages** | AWS-managed paths | SSM agent registration and command channel (often via VPC interface endpoints in private subnets). |
| **Execution nodes** → **Control plane internal API** | HTTPS or HTTP to internal listener | Heartbeat and optional future agent callbacks; restrict to VPC CIDR or worker/API SG. |
| **RDS** | Ingress from **control plane / worker** SG only | No direct access from execution nodes unless you explicitly justify a migration tool. |

### 3.3 Execution node ingress (critical)

- **Must:** Allow **Traefik / gateway** SG to reach the **workspace-facing ports** on the node.
- **Must not:** Expose workspace IDE to `0.0.0.0/0` unless product explicitly requires public IP and accepts the risk.
- **SSM:** Prefer **VPC interface endpoints** for SSM and EC2Messages so nodes in private subnets stay manageable without public IPs.

### 3.4 RDS and S3 / VPC endpoints

| Component | Recommendation |
|-----------|----------------|
| **RDS** | Private subnets; SG allows only application + worker + migration roles. Execution nodes **do not** need RDS connectivity for normal DevNest workspace operation. |
| **S3** | Use **VPC gateway or interface endpoint** for data-plane heavy paths if policy requires no public internet egress from private subnets. |
| **ECR / API** | Interface endpoints optional but reduce NAT dependency and data egress charges. |

---

## 4. IAM instance profile requirements (execution nodes)

Attach an **instance profile** to each execution EC2 with least privilege. Below is a **logical** policy outline — actual ARNs and resource constraints are account-specific.

### 4.1 Required (typical DevNest + SSM)

| Capability | Notes |
|------------|--------|
| **SSM core** | `AmazonSSMManagedInstanceCore` (or equivalent minimal policy) so operators and the control plane can use **Run Command** / Session Manager without SSH keys on every path. |
| **Describe instance / tags** | Often included in SSM / EC2 read bundles used by agents or bootstrap; keep read-only where possible. |

### 4.2 S3 snapshot bucket access

- **Grant:** `s3:GetObject`, `s3:PutObject`, `s3:AbortMultipartUpload`, `s3:ListBucket` (prefix-scoped), **`kms:Decrypt` / `kms:GenerateDataKey`** if the bucket uses SSE-KMS.
- **Scope:** Single bucket (and optional prefix) for workspace snapshot archives — **no** `s3:*` on `*`.

### 4.3 ECR / image pull (if pulls run on the node)

- **Grant:** `ecr:GetAuthorizationToken` (often `*` resource as required by AWS), plus `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` on the **repository ARNs** used for workspace images.

### 4.4 CloudWatch Logs (optional)

- **Grant:** `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` on a **named** log group prefix for node/agent diagnostics.

### 4.5 What execution nodes should **not** need by default

| Excluded | Rationale |
|----------|-----------|
| **Broad RDS access** | Registry and workspace state live in the control plane DB; nodes run containers and disks. Any RDS access from nodes should be a **separate** reviewed exception with threat model. |
| **Control-plane admin IAM** | Nodes must not assume operator or CI roles. |

---

## 5. Node registration checklist

Before marking a node **READY** and **schedulable**, verify the following (against your internal API and DB conventions).

### 5.1 EC2 and cloud metadata

- [ ] Instance **running** in the intended **account / region / VPC / subnet**.
- [ ] **SSM Online** (if SSM is the operational path) or documented SSH access model.
- [ ] **Instance profile** attached with policies from Section 4.
- [ ] **Security groups** attached per Section 3.

### 5.2 `execution_node` row (registry)

- [ ] **`node_key`** — unique string (stable identity for placement and runtime binding); align with product naming (e.g. `node-2`, not reused from retired nodes without a clear lifecycle).
- [ ] **`provider_instance_id`** — EC2 instance id.
- [ ] **`region`**, **`private_ip`**, **`public_ip`** (if used) — match `describe-instances`.
- [ ] **`execution_mode`** — matches how the worker will reach Docker (`ssm_docker`, `ssh_docker`, `local_docker`, etc.). **Must** match AMI and connectivity reality.
- [ ] **Capacity fields** — `allocatable_*`, `max_workspaces`, etc., consistent with instance size and policy.
- [ ] **`status`** — e.g. **READY** only when bootstrap complete.
- [ ] **`schedulable`** — `true` only when you intend placement to use the node.

### 5.3 Heartbeat

- [ ] Heartbeat path configured from node (or sidecar) to **`POST /internal/execution-nodes/heartbeat`** with **infrastructure-scoped** internal API key (stored in secrets manager / env — not in this doc).
- [ ] **`last_heartbeat_at`** updates on an expected interval before turning on aggressive placement gates (if any).

### 5.4 Routing readiness (before user traffic)

- [ ] Traefik / route-admin can reach **private IP + port** from the gateway’s network position (SG + routing).
- [ ] Smoke: internal `nc` or equivalent from a peer in the **same path as Traefik** to the workspace port (after a test workspace exists).

---

## 6. Operator runbooks

Use your deployment’s **base URL** and **internal API key** from secure configuration (never commit them). Replace placeholders below.

**Convention:** `API` = control plane base URL (e.g. `https://api.example.internal`). Internal routes require header **`X-Internal-API-Key`** with a key allowed for **infrastructure** scope (or deployment’s documented legacy key).

### 6.1 Register a node (existing EC2)

Typical flow (exact JSON fields depend on your API version):

1. Ensure EC2 exists, SSM/SSH works, SG + IAM from Sections 3–4 applied.
2. Call **`POST /internal/execution-nodes/register-existing`** (or **`provision`** if creating new EC2 via DevNest) with instance id, desired `node_key`, `execution_mode`, etc.
3. Confirm with **`GET /internal/execution-nodes/`** that the node appears with expected IPs and status.

**Verify:** Registry list shows one row per instance; no duplicate `node_key`.

### 6.2 Verify heartbeat

1. From the node (or automation), ensure periodic **`POST /internal/execution-nodes/heartbeat`** with body including `node_key`, `docker_ok`, and optional telemetry fields per API schema.
2. On the control plane, confirm **`last_heartbeat_at`** advances (DB query or list endpoint if exposed).

**Verify:** Stopping the heartbeat process causes `last_heartbeat_at` to age; restarting restores updates.

### 6.3 Drain a node

1. Call **`POST /internal/execution-nodes/drain`** with `node_key` or `node_id` per API.
2. Confirm node is **not** chosen for **new** placements (per product semantics: **DRAINING** + `schedulable=false` or equivalent).
3. Wait for existing workspaces to be stopped or migrated per product policy before decommissioning.

**Verify:** New workspace creates do not bind to drained node; existing workloads behave as documented.

### 6.4 Deregister a node

1. Call **`POST /internal/execution-nodes/deregister`** when the node should leave the schedulable pool without necessarily stopping EC2 (per API semantics).
2. Confirm registry status and schedulable flags.

**Verify:** Node no longer receives new placements; API returns expected state.

### 6.5 Terminate a node (EC2 lifecycle)

1. Ensure workloads are cleared and node is drained/deregistered per policy.
2. Call **`POST /internal/execution-nodes/terminate`** if DevNest drives AWS termination; otherwise terminate in AWS console/API and reconcile registry via **`sync`** or documented cleanup.

**Verify:** Instance state and `execution_node` row match expectations; no orphaned Traefik backends for workspaces that pointed at that node.

---

## 7. Rollback plan

| Situation | Action |
|-----------|--------|
| **Before any user workloads on new node** | Set **`schedulable=false`**, **drain**, **deregister** via internal API; terminate EC2 if created; remove any test Traefik routes. |
| **After bad placement** | Stop affected workspaces via product APIs; drain new node; fix registry or `execution_mode`; re-run placement tests. |
| **After routing change** | Revert route-admin / Traefik dynamic config to previous revision; bounce Traefik if required by deploy process. |
| **IAM / SG mistake** | Revert IaC PR or detach policy; restore previous SG on instances — prefer **additive** SGs for first rollout to avoid touching node 1. |
| **Documentation-only rollback** | Revert or archive this markdown file — no runtime effect. |

**Principle:** Keep **single-node** production valid at all times; introduce node 2 behind **drain** and **schedulable** flags until validation completes.

---

## 8. Phase 3b definition of done

Phase 3b is complete when **all** of the following are true (in a staging or dedicated validation environment first):

1. **Fleet:** At least **two** execution nodes registered with correct EC2 metadata and **`execution_mode`** aligned with connectivity.
2. **Heartbeat:** Each node’s **`last_heartbeat_at`** updates on the expected interval without operator manual POSTs (automation or worker-side emitter as designed).
3. **Placement:** New workspaces can be placed on **either** node under capacity and spreading rules; **no overbooking** under agreed load test.
4. **Execution:** Worker runs full workspace lifecycle (create/open/save/stop) on a workspace **bound to the second node** via the normal orchestration path.
5. **Routing:** Traefik serves a workspace hosted on the second node via **route-admin–managed** dynamic configuration; first node unchanged for existing workspaces.
6. **Snapshots:** Save and download snapshot for a workspace on the second node; objects in S3 with correct access from control plane and/or node per design.
7. **Operations:** Drain → no new placements; deregister / terminate runbooks executed without orphan routes or stuck DB rows.
8. **Single-node safety:** With second node **absent** or **fully drained and deregistered**, the deployment behaves as today’s **single-node** baseline.

---

## Appendix A — Files changed (Step A)

| File | Action |
|------|--------|
| `docs/PHASE_3B_FLEET_RUNBOOK.md` | **Added** (this document). |

No application code, infrastructure templates, or compose files were modified in Step A.

---

## Appendix B — Runbook sections (index)

1. Purpose and scope  
2. Target N-node execution fleet architecture  
3. Security group matrix  
4. IAM instance profile requirements (execution nodes)  
5. Node registration checklist  
6. Operator runbooks (register, heartbeat, drain, deregister, terminate)  
7. Rollback plan  
8. Phase 3b definition of done  
Appendix A — Files changed  
Appendix B — This index  

---

## Appendix C — Open questions before Step 1 (infra) work

Resolve these in design or infra PRs **before** or **alongside** the first EC2 change:

1. **Ingress topology:** Will Traefik run **inside** the same VPC as execution nodes, or behind a TGW / NLB? This fixes the exact **source SG** for node ingress.
2. **Private vs public IPs:** Do execution nodes use **only private IPs** with Traefik peered in VPC (recommended), or public IPs for simplicity (higher exposure)?
3. **Execution mode canonical for fleet:** Is **SSM** the single supported mode for EC2 fleet in v1 of Phase 3b, with SSH legacy only for break-glass?
4. **Heartbeat source:** Heartbeats from **node cron**, **systemd timer**, **sidecar container**, or **worker-only** HTTP? (Affects SG from node → API and operational ownership.)
5. **Route-admin authority:** Which component **owns** the write to Traefik dynamic files — route-admin only, or also API fallback? Conflict resolution on partial failures?
6. **Snapshot bytes path:** Are large snapshot objects streamed **worker → S3** only, or does the **node** upload directly? This determines whether S3 VPC endpoint is mandatory on node SG.
7. **KMS:** Is the snapshot bucket using **SSE-KMS**? If yes, instance profile and worker role need aligned key policies.
8. **Multi-region:** Is Phase 3b **single-region** only? (Cross-region placement and S3 latency are out of scope unless explicitly in roadmap.)
9. **Autoscaler interaction:** If autoscaler provisions EC2, who is responsible for **register-existing** — automation hook, operator, or CI?
10. **Placement freshness gate:** Will **`DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT`** (or equivalent) stay **off** in production until fleet heartbeats are proven stable?

---

*End of Phase 3b Step A runbook.*
