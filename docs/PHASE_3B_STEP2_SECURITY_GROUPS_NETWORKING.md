# Phase 3b Step 2 — Security groups and networking plan (docs / infra planning only)

**Status:** Design and validation guidance. **No** application code changes, **no** second `execution_node` registration, **no** secrets in this document.

**Prerequisites:**

- [PHASE_3B_FLEET_RUNBOOK.md](./PHASE_3B_FLEET_RUNBOOK.md) — fleet roles and high-level SG matrix.
- [Phase 3b Step 1 — EC2 execution-node template](./PHASE_3B_STEP1_EXECUTION_NODE_EC2.md) — EC2 host bootstrap before tightening rules.
- [Phase 3b Step 3 — IAM for execution nodes](./PHASE_3B_STEP3_IAM_EXECUTION_NODES.md) — instance profile and S3/ECR/SSM policies aligned with this network plan.
- [Phase 3b Step 4 — Catalog registration (node 2)](./PHASE_3B_STEP4_CATALOG_REGISTRATION_NODE2.md) — register in DB with `schedulable=false` before routing/workloads.

**Goal:** Define **exact** security group and routing expectations so execution nodes can run workspaces while **blocking** public internet access to workspace IDE ports, and so **Traefik**, **workers**, and **nodes** can reach only what they need.

---

## 1. Security groups to define (logical tiers)

Create or reuse **distinct** security groups (names are examples; use your naming standard).

| SG name (example) | Attaches to | Purpose |
|-------------------|-------------|---------|
| **`sg-control-plane-api`** | Load balancer target / API EC2 or ECS tasks | Inbound user/API traffic to FastAPI; outbound to RDS, S3, internal calls. |
| **`sg-worker`** | Workspace job worker (EC2/ECS) | Outbound to RDS, S3, **SSM API**, execution nodes (SSM path is **not** TCP to node for default SSM), **HTTPS to internal API** for heartbeats; inbound usually none or health only. |
| **`sg-traefik-gateway`** | Traefik (or ALB in front of Traefik) | Inbound **443/80** (or **9081** for dev HTTP gateway per compose); outbound to **execution node workspace ports** and optionally to API. |
| **`sg-execution-node`** | Each EC2 execution fleet member | Inbound **only** from Traefik (and optional bastion for SSH); outbound to S3, ECR, AWS APIs, **internal API** for heartbeat. |
| **`sg-rds`** | RDS / Aurora | Inbound **5432** (or your engine port) **only** from `sg-control-plane-api` and `sg-worker` — **not** from `sg-execution-node` unless you have a documented exception. |

**Note:** If API and worker share one process (e.g. small dev), you may merge **`sg-control-plane-api`** and **`sg-worker`**; production should separate blast radius.

---

## 2. Allowed traffic (authoritative rules)

### 2.1 Traefik / gateway → execution node (user traffic)

| Direction | Protocol | Ports | Source | Destination | Notes |
|-----------|----------|-------|--------|---------------|-------|
| Ingress | TCP | **Published host ports** for workspace containers | `sg-traefik-gateway` | `sg-execution-node` | DevNest maps each workspace to a **host-published** TCP port (Docker `-p`) pointing at container **8080** (see §3). Traefik backends must target **node private IP + published port**. |
| **Blocked** | TCP | Same workspace ports | `0.0.0.0/0` or `::/0` | `sg-execution-node` | **Do not** allow the world to reach IDE ports. |

**Product reference:** In-container IDE listens on **`WORKSPACE_IDE_CONTAINER_PORT` = 8080** (`backend/app/libs/runtime/models.py`). Published port on the host is **per workspace** (orchestrator/runtime); Traefik must use the **published** port from route-admin / runtime state, not assume a single static port for all workspaces.

### 2.2 Worker / control plane → execution node (management)

| Path | Inbound to node from worker? | Notes |
|------|------------------------------|-------|
| **SSM (default for `ssm_docker`)** | **No** inbound TCP from worker to node for command delivery | SSM agent on node **outbound** to AWS SSM service (via VPC endpoints or internet). Worker uses **AWS API** `SendCommand`. |
| **SSH (`ssh_docker`)** | **Yes** — TCP **22** (or custom) | Source = **`sg-worker`** and/or **bastion SG only** — never `0.0.0.0/0`. |
| **Future node agent (HTTP/mTLS)** | **Yes** — narrow TCP (e.g. **8443** from `sg-worker` only**) | Not implemented in this doc; plan a dedicated listener SG rule. |

### 2.3 Execution node → AWS and control plane

| Destination | Protocol | Ports | Purpose |
|-------------|----------|-------|---------|
| **S3** | TCP **443** | HTTPS to `s3.*` or VPC gateway endpoint | Snapshot artifacts if node role performs S3; else still useful for logs/bootstrap. |
| **ECR** | TCP **443** | `api.ecr.*`, registry endpoints | Pull `devnest/workspace` (or configured image). |
| **STS / EC2 / general AWS APIs** | TCP **443** | As required by instance profile | Minimal policies. |
| **SSM / EC2Messages / SSMMessages** | TCP **443** | VPC **interface endpoints** (recommended) or public endpoints | SSM agent and Session Manager. |
| **Internal API (heartbeat)** | TCP **443** or **8000** | HTTPS or HTTP to **private** API URL (`INTERNAL_API_BASE_URL`) | Source `sg-execution-node` egress; destination = API NLB/ALB or private IP SG inbound from **VPC CIDR** or **`sg-execution-node`** only — **not** open to the internet on the API listener used for heartbeat. |

### 2.4 Control plane / worker → RDS

- **Only** `sg-control-plane-api` and `sg-worker` → `sg-rds` on DB port.
- Execution nodes **do not** get RDS rules for standard DevNest.

### 2.5 Internet → Traefik / gateway (edge)

| Ingress | Ports | Notes |
|---------|-------|------|
| Users | **443** (and **80** redirect if used) | Public edge. |
| Dev / compose-style gateway | **9081** (example from `docker-compose.integration.yml` Traefik published port) | Map to your real edge; not required on execution nodes. |

---

## 3. Port matrix (reference)

| Flow | Port(s) | Where |
|------|-----------|--------|
| **Browser → Traefik** | **443** / **80** | Public or corp edge. |
| **Browser / client → dev gateway** | **9081** (example) | Local/integration Traefik mapped to 80 in compose — use your deploy map. |
| **Traefik → workspace on node** | **Dynamic TCP** (published host port) → container **8080** | **8080** is `WORKSPACE_IDE_CONTAINER_PORT` inside the container; Traefik target is **host:published_port**. |
| **code-server HTTP health** | **8080** path inside container (e.g. `/healthz`) | Probes from worker/API may hit **workspace_ip:8080** on an overlay network path; not the same as SG “host port” unless probing from host netns — align probes with your topology doc. |
| **SSH management** | **22/tcp** | **Only** if `execution_mode` includes SSH; restrict source SG. |
| **SSM** | **443** outbound from node to AWS | No inbound **listener** on node for SSM commands. |
| **PostgreSQL** | **5432** (typical) | RDS SG ← API/worker only. |
| **Internal API** | **8000** (common dev) / **443** (TLS) | Heartbeat `POST /internal/execution-nodes/heartbeat` from node to API. |

---

## 4. Network routing (conceptual)

1. **Execution nodes** live in **private subnets** with **no** public IP **or** with public IP **but** SG still blocks IDE ports from `0.0.0.0/0` (defense in depth: prefer private-only nodes).
2. **Traefik** can reach **node private IP** (same VPC, peering, TGW, or NLB hop — your VPC design).
3. **Nodes** reach **API** via **private** DNS (VPC internal ALB/NLB, or service discovery), not via the public internet URL unless unavoidable (avoid).

---

## 5. Validation commands

Replace placeholders: `<TRAEFIK_HOST>`, `<NODE_PRIVATE_IP>`, `<PUBLISHED_PORT>`, `<REGION>`, `<BUCKET>`, `<API_PRIVATE_URL>` (no secrets in commands).

### 5.1 Traefik (or peer in `sg-traefik-gateway`) can reach node private IP and workspace port

From a host or container that has **`sg-traefik-gateway`** (or same SG attachment as production Traefik data plane):

```bash
nc -vz -w 5 <NODE_PRIVATE_IP> <PUBLISHED_PORT>
```

Optional HTTP check (if TLS termination is at Traefik and backend is plain HTTP):

```bash
curl -v --max-time 5 "http://<NODE_PRIVATE_IP>:<PUBLISHED_PORT>/healthz" || true
```

**Expect:** TCP success when a workspace is running and route-admin has programmed the backend; **fail** before bring-up is expected.

### 5.2 Public internet cannot reach workspace IDE port

From **outside** the VPC (e.g. laptop on internet, or a host with no VPC path):

```bash
nc -vz -w 5 <NODE_PUBLIC_IP_OR_EIP> <PUBLISHED_PORT>   # if node has a public IP for test only
# OR from random VPS:
nc -vz -w 5 <NODE_PRIVATE_IP> <PUBLISHED_PORT>        # should FAIL from internet (private IP not routable)
```

**Expect:**

- If the node has **no** public IP: internet test to private IP is N/A; confirm **security group has no** `0.0.0.0/0` ingress on workspace ports.
- If the node has a **public IP** for lab: SG must **still** deny `0.0.0.0/0` on `<PUBLISHED_PORT>`; only Traefik SG allowed.

**AWS describe check (no secrets):**

```bash
aws ec2 describe-security-groups \
  --region <REGION> \
  --group-ids <SG_EXECUTION_NODE_ID> \
  --query 'SecurityGroups[0].IpPermissions'
```

Inspect for accidental `0.0.0.0/0` on high ports or 8080.

### 5.3 Node can reach S3 and backend heartbeat endpoint

Run on instance via SSM (see Step 1 `AWS-RunShellScript` pattern):

```bash
# S3 (read list only; requires IAM on instance profile)
aws s3 ls "s3://<BUCKET>/" --max-items 3

# API reachability (no secret in URL; use header from SSM Parameter in real ops — here: TCP only)
nc -vz -w 5 $(python3 -c "from urllib.parse import urlparse; import os; print(urlparse(os.environ['API_URL_FOR_NC']).hostname)") 443
```

For a **TCP-only** smoke without embedding a key in command history:

```bash
getent hosts <api-hostname-from-internal-url>
nc -vz -w 5 <api-private-ip-or-hostname> 443
```

**Expect:** S3 succeeds if policy allows; TCP to API listener succeeds from node subnet (SG + NACL + route tables). **HTTP 200** on heartbeat requires `X-Internal-API-Key` — perform that check from a secure operator session, not logged in CI.

---

## 6. Rollback steps

| Step | Action |
|------|--------|
| 1 | **Revert** Terraform / CloudFormation / console changes to SG rules (prefer git revert on IaC PR). |
| 2 | If a second node was **already** registered (outside Step 2 scope): **drain** and **deregister** via internal APIs (see fleet runbook §6). |
| 3 | **Terminate** experimental EC2 if the instance was only for SG testing. |
| 4 | Confirm **node 1** / single-node deployment SGs were **not** modified, or restore previous revision if changed by mistake. |

**Principle:** Apply **new** SGs to **new** instances first; avoid in-place edits to production node 1 SG until validated.

---

## 7. Definition of done (Step 2 only)

- [ ] Written SG IDs and **ingress/egress** rules exist in IaC or runbook tables for **`sg-traefik-gateway`**, **`sg-execution-node`**, **`sg-worker`**, **`sg-control-plane-api`**, **`sg-rds`**.  
- [ ] **Traefik → node** path documented with **dynamic published port** + private IP.  
- [ ] **No** `0.0.0.0/0` ingress on workspace host ports on execution nodes.  
- [ ] **SSM** path documented (endpoints optional but recommended); **SSH** documented only if used, with restricted sources.  
- [ ] **Node → S3 / ECR / API** egress documented and validated with §5 commands.  
- [ ] **RDS** remains reachable **only** from control plane and worker SGs.  
- [ ] Rollback path in §6 is agreed with operators.

---

## 8. Files touched by this step

| File | Role |
|------|------|
| `docs/PHASE_3B_STEP2_SECURITY_GROUPS_NETWORKING.md` | This Step 2 SG and networking plan. |

---

## 9. Open questions (carry to implementation)

- Exact **published port** range (dynamic per workspace vs narrow range) for SG rules — some orgs use a **pre-allocated TCP window** per node.  
- Whether **Traefik** and **worker** share a VPC **subnet** with nodes or use **TGW** — affects whether SG references suffice or CIDRs are needed.  
- **TLS**: Traefik → node plain HTTP vs re-encrypt — affects whether port 443 appears on the node (usually not for workspace backend).  
- **IPv6**: If enabled, duplicate matrix for `::/0` blocks and v6 SG rules.

---

*Phase 3b Step 2 — Security groups and networking. Documentation only.*
