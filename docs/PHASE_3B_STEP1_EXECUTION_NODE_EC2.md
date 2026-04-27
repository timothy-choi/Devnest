# Phase 3b Step 1 — EC2 execution-node template (infra / docs only)

**Status:** Documentation and operator guidance. **Does not** register a second node in DevNest, **does not** change application code, scheduler, routing, or Docker runtime in this repository.

**Prerequisite:** Read [PHASE_3B_FLEET_RUNBOOK.md](./PHASE_3B_FLEET_RUNBOOK.md) (security group matrix, IAM outline, operator verbs). For detailed SG and port planning, see [Phase 3b Step 2 — Security groups and networking](./PHASE_3B_STEP2_SECURITY_GROUPS_NETWORKING.md). For IAM instance profile and example policies, see [Phase 3b Step 3 — IAM for execution nodes](./PHASE_3B_STEP3_IAM_EXECUTION_NODES.md). For registering a second node in the catalog without placements, see [Phase 3b Step 4 — Catalog registration](./PHASE_3B_STEP4_CATALOG_REGISTRATION_NODE2.md). For heartbeat automation while the node stays non-schedulable, see [Phase 3b Step 5 — Heartbeat from node 2](./PHASE_3B_STEP5_HEARTBEAT_NODE2.md).

**Goal:** A **reproducible** path to launch an additional EC2 instance that *could* become execution **node 2** (or any `node_key`) **later**, after Step 2+ (SG/IAM hardening, registry, routing). This step stops at a **validated blank execution host**.

---

## 1. What this step delivers

| Deliverable | Description |
|-------------|-------------|
| **Instance** | EC2 in the correct VPC/subnet, with instance profile and SGs per fleet runbook (applied outside this repo or via your IaC). |
| **Software** | Docker Engine, SSM agent online, optional AWS CLI for S3 smoke tests, workspace project base directory. |
| **Alignment** | Same conventions as integration compose for **project base path** and **workspace container image name** (see below). |
| **Explicitly not done here** | `POST /internal/execution-nodes/register-existing`, scheduler changes, Traefik backends, or app deploys. |

---

## 2. Provisioning options (choose one)

### 2.1 AWS Console / EC2 Launch Wizard

1. Create launch template or one-off instance from **Ubuntu Server LTS** (see §3.1).
2. Attach **instance profile** with IAM from fleet runbook §4.
3. Attach **security groups** from fleet runbook §3.
4. Paste **user data** shell (§3.2) or equivalent bootstrap (cloud-init).
5. Launch; wait **running**; verify §6.

### 2.2 Infrastructure-as-code (recommended for N nodes)

- Add a module or stack: `execution_node_fleet_member` with variables for subnet, SG ids, instance profile, `node_key` label (tag only — **not** DevNest registry yet), disk size.
- Output: `instance_id`, `private_ip`, `availability_zone`.
- **This repo:** no Terraform/CDK files are required in Step 1; keep IaC in your platform repo or add in a later PR if policy requires it here.

### 2.3 AMI strategy

- **Phase 3b validation:** Use **official Ubuntu LTS** + user-data to install Docker and SSM (simplest audit trail).
- **Later:** Build a **golden AMI** (Packer) with Docker + SSM + pre-pulled `devnest/workspace` image for faster scale-out.

---

## 3. Required base setup

### 3.1 Ubuntu / AMI choice

| Decision | Guidance |
|----------|----------|
| **OS** | **Ubuntu Server LTS** (22.04 or 24.04) x86_64 in the **same region** as control plane, RDS, and S3 bucket (unless you have a deliberate multi-region design). |
| **AMI source** | Official Canonical Ubuntu AMIs (`/ubuntu/images/hvm-ssd/ubuntu-*-amd64-server-*`) or your org’s hardened baseline. |
| **Instance size** | Match or exceed your **first** execution node class (CPU/RAM/disk) so capacity fields can be copied later without surprises. |
| **Root volume** | Large enough for Docker layers + several workspaces’ bind mounts; **gp3** recommended. |

### 3.2 Docker installation

- Follow **Docker Engine** official docs for Ubuntu (`https://docs.docker.com/engine/install/ubuntu/`).
- Enable and start `docker` service; add any OS user that will run admin commands to `docker` group **only** if required (prefer rootless or SSM as root per your security model).
- **Smoke:** `sudo docker run --rm hello-world` (or `docker ps` after install).

**User-data sketch (illustrative — test in a throwaway instance first):**

```bash
#!/bin/bash
set -euxo pipefail
apt-get update -y
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
# … follow Docker’s official install steps for your Ubuntu major version …
systemctl enable --now docker
```

### 3.3 Workspace project base path

DevNest expects a **host directory** for per-workspace bind mounts, exposed to the worker/orchestrator as **`WORKSPACE_PROJECTS_BASE`** (see `docker-compose.integration.yml`: e.g. `/var/lib/devnest/workspace-projects`).

| Requirement | Action |
|-------------|--------|
| **Path** | Create the **same absolute path** you will configure on the worker when this node is in the pool (e.g. `/var/lib/devnest/workspace-projects`). |
| **Ownership / mode** | Must be writable by the identity that creates workspace directories (often root when using Docker from SSM/SSH as root). Align with node 1. |
| **Disk** | Dedicated volume or large root; snapshots are S3-backed — live files stay **node-local**. |

### 3.4 code-server / workspace image requirements

- Workspace containers are built from **`Dockerfile.workspace`** in this repo; default image reference in integration is **`devnest/workspace:latest`** (see compose anchor `DEVNEST_WORKSPACE_CONTAINER_IMAGE`).
- **On the new EC2:** either:
  - **`docker pull`** the same tag your control plane uses (`DEVNEST_WORKSPACE_CONTAINER_IMAGE`), **or**
  - rely on first-workspace pull (slower; ensure ECR/SG allows pull at runtime).
- **code-server** runs **inside** that image; the host only needs Docker.

### 3.5 AWS CLI / SSM agent requirements

| Component | Notes |
|-----------|--------|
| **SSM Agent** | Ubuntu AMIs often include `amazon-ssm-agent`; ensure **`systemctl status amazon-ssm-agent`** is active. Required for **`execution_mode=ssm_docker`** orchestration from the worker. |
| **AWS CLI v2** | Optional on node for **manual** S3 validation (§6.4); instance profile supplies credentials. Not required for DevNest core if worker performs all S3 APIs. |

---

## 4. Required environment / config (on the instance and for later registration)

These values are **documentation** for when you register the node — **do not** paste secrets into tickets.

| Item | Purpose |
|------|---------|
| **`node_key`** | Stable string you will use in `execution_node` (e.g. `node-2`). **Unique** across the fleet. |
| **`execution_mode`** | Must match how the worker reaches Docker on this host (typically **`ssm_docker`** for EC2 fleet). |
| **`WORKSPACE_PROJECTS_BASE`** | Host path for bind mounts; must match worker/orchestrator config for workspaces placed on this node. |
| **S3 bucket / prefix / region** | Snapshot storage settings must match control plane (`DEVNEST_S3_*` or equivalent) if the **node** performs S3 uploads/downloads; if only the worker uses S3, node policy can stay narrower until you confirm design (see fleet runbook open questions). |
| **`INTERNAL_API_BASE_URL`** (for heartbeat later) | URL reachable **from this instance** to the DevNest API (e.g. private VPC DNS). Used when you enable heartbeat automation on the node — **not** required to complete Step 1 validation. |
| **Internal API key** | Stored in **Secrets Manager / SSM Parameter / env** on the node or sidecar when you implement heartbeat — **never** in this file. |

**Important:** Step 1 **does not** call DevNest registration APIs. Tags on EC2 (`devnest:node_key=…`) are optional for your CMDB only.

---

## 5. Node 2 provisioning checklist (pre–registry)

Use this as a literal checklist before any `register-existing` call in a later step.

- [ ] VPC, subnet, and **private** IP strategy match Traefik → node path (fleet runbook §3).
- [ ] Security groups attached per fleet runbook.
- [ ] Instance profile attached; **SSM** works from operator account.
- [ ] Ubuntu LTS AMI launched; instance **running**.
- [ ] Docker installed and **`docker ps`** works.
- [ ] **`WORKSPACE_PROJECTS_BASE`** directory exists and is writable.
- [ ] Workspace image pulled **or** pull path verified (ECR/SG egress).
- [ ] (Optional) AWS CLI on instance can **`aws s3 ls`** against snapshot prefix using instance role.
- [ ] **Do not** call `POST /internal/execution-nodes/register-existing` until a later Phase 3b step.

---

## 6. Validation commands

Replace placeholders: `<INSTANCE_ID>`, `<REGION>`, `<BUCKET>`, `<PROJECT_BASE>`.

### 6.1 Instance is running

```bash
aws ec2 describe-instances \
  --region <REGION> \
  --instance-ids <INSTANCE_ID> \
  --query 'Reservations[0].Instances[0].{State:State.Name,PrivateIp:PrivateIpAddress,Az:Placement.AvailabilityZone}' \
  --output table
```

Expected: `State` = `running`; note **PrivateIp** for future Traefik backend.

### 6.2 SSM online

```bash
aws ssm describe-instance-information \
  --region <REGION> \
  --filters Key=InstanceIds,Values=<INSTANCE_ID> \
  --query 'InstanceInformationList[0].PingStatus' \
  --output text
```

Expected: `Online`.

**Interactive shell (operator):**

```bash
aws ssm start-session --region <REGION> --target <INSTANCE_ID>
```

### 6.3 Docker works

Run via SSM (example using AWS-RunShellScript):

```bash
aws ssm send-command \
  --region <REGION> \
  --instance-ids <INSTANCE_ID> \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["docker info","docker run --rm hello-world"]' \
  --query 'Command.CommandId' \
  --output text
```

Then `aws ssm list-command-invocations` / get-command-invocation for success.

### 6.4 S3 access works (if instance profile includes S3)

```bash
aws ssm send-command \
  --region <REGION> \
  --instance-ids <INSTANCE_ID> \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["aws s3 ls s3://<BUCKET>/<optional-prefix>/ --max-items 5"]' \
  --query 'Command.CommandId' \
  --output text
```

If this fails, fix IAM **before** relying on node-side snapshot code paths.

### 6.5 Project directory exists and is writable

```bash
aws ssm send-command \
  --region <REGION> \
  --instance-ids <INSTANCE_ID> \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["sudo mkdir -p <PROJECT_BASE>","sudo touch <PROJECT_BASE>/.devnest-write-test","sudo rm <PROJECT_BASE>/.devnest-write-test","ls -la <PROJECT_BASE>"]' \
  --query 'Command.CommandId' \
  --output text
```

Use the same `<PROJECT_BASE>` as `WORKSPACE_PROJECTS_BASE` (e.g. `/var/lib/devnest/workspace-projects`).

---

## 7. Rollback steps (Step 1 only)

| Action | When |
|--------|------|
| **Stop / terminate EC2** | If validation fails or the instance was exploratory. |
| **Release EIPs** | If a public EIP was attached for testing. |
| **Remove IaC** | If you created a throwaway stack/module. |
| **No DevNest rollback** | No registry row was created in Step 1; scheduler and routing unchanged. |

---

## 8. What happens next (not Step 1)

- **Step 2+ (from fleet checklist):** tighten SGs/IAM in IaC, **register** node via internal API, enable heartbeat automation, placement tests, Traefik route to private IP, snapshot E2E.

---

## 9. Files touched by this document

| File | Role |
|------|------|
| `docs/PHASE_3B_STEP1_EXECUTION_NODE_EC2.md` | This Step 1 template / runbook. |

---

*Phase 3b Step 1 — EC2 execution-node template. No application code changes.*
