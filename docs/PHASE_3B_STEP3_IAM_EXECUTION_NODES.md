# Phase 3b Step 3 — IAM instance profile for EC2 execution nodes (docs / infra planning only)

**Status:** Design and example policies for **future** fleet EC2 instances. **No** application code, **no** real ARNs or secrets, **no** `execution_node` registration in this step.

**Prerequisites:**

- [PHASE_3B_FLEET_RUNBOOK.md](./PHASE_3B_FLEET_RUNBOOK.md) — §4 IAM outline.
- [PHASE_3B_STEP1_EXECUTION_NODE_EC2.md](./PHASE_3B_STEP1_EXECUTION_NODE_EC2.md) — instance bootstrap.
- [PHASE_3B_STEP2_SECURITY_GROUPS_NETWORKING.md](./PHASE_3B_STEP2_SECURITY_GROUPS_NETWORKING.md) — network path for API/S3.

**Goal:** Attach a **least-privilege** IAM role (via **instance profile**) to each execution EC2 so SSM, optional S3 snapshot I/O, optional ECR pull, and optional CloudWatch logging work — without admin or broad data access.

---

## 1. IAM role / policy matrix (logical)

| Capability | AWS surface | Attach to execution role? | Notes |
|------------|-------------|----------------------------|--------|
| **SSM managed instance** | AWS managed policy **`AmazonSSMManagedInstanceCore`** (or org-hardened equivalent) | **Yes** (recommended) | Session Manager + **SSM Run Command** for `ssm_docker` orchestration. |
| **S3 snapshot bucket (prefix-scoped)** | Custom inline or customer-managed policy | **Yes**, if nodes perform S3 I/O | Match control plane bucket + prefix (`DEVNEST_S3_*`); **no** `s3:*` on `*`. |
| **ECR image pull** | Custom policy on repository ARNs | **Yes**, if images are in ECR | `ecr:GetAuthorizationToken` + layer pulls on **named** repos only. |
| **CloudWatch Logs** | Custom policy on log group ARN prefix | **Optional** | Agent / bootstrap diagnostics. |
| **STS identity check** | `sts:GetCallerIdentity` | **Yes** (low risk) | Operator and automation validation (`aws sts get-caller-identity`). |
| **RDS** | RDS API or DB connect | **No** (default) | Execution nodes do not need DB credentials for DevNest v1 fleet; add only with a written exception. |
| **AdministratorAccess** | — | **Never** on execution nodes | Blast radius includes every AWS API in the account. |

**Instance profile:** Create **`iam:PassRole`** only on CI/deploy principals that attach the profile to EC2 — not inside the execution policy itself.

---

## 2. Explicitly avoid

| Anti-pattern | Why |
|--------------|-----|
| **`AdministratorAccess`**, `PowerUserAccess`, `IAMFullAccess`** | Full account compromise if the instance is abused. |
| **`s3:*` on `arn:aws:s3:::*`** | Cross-bucket exfiltration; use **one bucket + prefix**. |
| **`s3:ListAllMyBuckets`** | Unnecessary enumeration; omit unless you have a compliance requirement. |
| **`rds:*`, `rds-db:connect`**, wide **`secretsmanager:GetSecretValue`** on `*` | Nodes should not read application DB or all secrets by default. |
| **`ec2:*` on `*`** | Instance can mutate VPC peers, SGs, or other instances. |
| **Unscoped `kms:Decrypt`** | Tie KMS grants to the **snapshot bucket CMK** only if using SSE-KMS. |

---

## 3. Example IAM policy documents (placeholders)

Replace placeholders **without** committing real values to git:

| Placeholder | Meaning |
|-------------|---------|
| `<ACCOUNT_ID>` | 12-digit AWS account id |
| `<REGION>` | e.g. `us-east-1` |
| `<BUCKET>` | S3 bucket name for DevNest snapshots (same as control plane config) |
| `<PREFIX>` | Key prefix for snapshots (may end with `/`; policies below use `*` after prefix) |
| `<ECR_REGISTRY_ACCOUNT>` | Often same as `<ACCOUNT_ID>`; use if ECR is cross-account |
| `<ECR_REPO_ARN>` | Full ARN of the workspace image repo, e.g. `arn:aws:ecr:<REGION>:<ACCOUNT_ID>:repository/devnest-workspace` |

### 3.1 Managed policy (attach separately)

Attach AWS managed **`AmazonSSMManagedInstanceCore`** to the execution role (or your security team’s vetted replacement). **Do not** paste its JSON here — it is maintained by AWS.

### 3.2 Custom policy — S3 snapshot prefix + STS (required when nodes touch S3)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "StsGetCallerIdentity",
      "Effect": "Allow",
      "Action": "sts:GetCallerIdentity",
      "Resource": "*"
    },
    {
      "Sid": "S3ListSnapshotPrefix",
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::<BUCKET>",
      "Condition": {
        "StringLike": {
          "s3:prefix": ["<PREFIX>*"]
        }
      }
    },
    {
      "Sid": "S3ObjectReadWriteSnapshotPrefix",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:AbortMultipartUpload",
        "s3:ListMultipartUploadParts"
      ],
      "Resource": "arn:aws:s3:::<BUCKET>/<PREFIX>*"
    }
  ]
}
```

**SSE-KMS (optional):** If the bucket uses **SSE-KMS**, add a second statement:

```json
{
  "Sid": "KmsForSnapshotBucket",
  "Effect": "Allow",
  "Action": ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"],
  "Resource": "arn:aws:kms:<REGION>:<ACCOUNT_ID>:key/<KMS_KEY_ID>"
}
```

Scope **`Resource`** to the **bucket’s** CMK only.

### 3.3 Custom policy — ECR pull (optional; attach only if nodes run `docker pull` against ECR)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EcrAuthToken",
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Sid": "EcrPullWorkspaceImage",
      "Effect": "Allow",
      "Action": [
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchCheckLayerAvailability"
      ],
      "Resource": "<ECR_REPO_ARN>"
    }
  ]
}
```

### 3.4 Custom policy — CloudWatch Logs (optional)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CwLogsExecutionNode",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams"
      ],
      "Resource": "arn:aws:logs:<REGION>:<ACCOUNT_ID>:log-group:/devnest/execution-node/*"
    }
  ]
}
```

Adjust the log group prefix to your naming standard.

### 3.5 Role trust relationship (EC2)

The **trust policy** on the **role** (not inline on instance) must allow `ec2.amazonaws.com`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "ec2.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

Create an **instance profile**, add the role, attach the profile to the execution EC2 instance.

---

## 4. Validation commands

Run **on the EC2 instance** via SSM Session Manager or `send-command` (see Step 1). Replace placeholders.

### 4.1 STS identity (confirms instance role)

```bash
aws sts get-caller-identity
```

**Expect:** `Account`, `Arn` containing `assumed-role` or `instance-profile` / role name you attached.

### 4.2 S3 list / copy round-trip (prefix only)

```bash
aws s3 ls "s3://<BUCKET>/<PREFIX>" --max-items 5

echo "devnest-iam-smoke" > /tmp/devnest-iam-smoke.txt
aws s3 cp /tmp/devnest-iam-smoke.txt "s3://<BUCKET>/<PREFIX>iam-smoke/$(date +%s).txt"
aws s3 rm "s3://<BUCKET>/<PREFIX>iam-smoke/" --recursive
```

**Expect:** List allowed within prefix; put/delete succeed. **Deny** if path escapes prefix (policy too broad or `Condition` wrong).

### 4.3 SSM instance information

```bash
aws ssm describe-instance-information \
  --region <REGION> \
  --filters Key=InstanceIds,Values=$(ec2-metadata --instance-id | cut -d' ' -f2) \
  --output table
```

(If `ec2-metadata` is unavailable, pass instance id from console.)

**Expect:** `PingStatus` = `Online`.

### 4.4 Docker image pull (ECR case)

```bash
aws ecr get-login-password --region <REGION> | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com
docker pull <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/<REPO_NAME>:<TAG>
```

**Expect:** Login and pull succeed when §3.3 is attached. For **Docker Hub**–only images, ECR policy is unnecessary.

---

## 5. Rollback steps

| Situation | Action |
|-----------|--------|
| **Wrong policy before any workload** | **Detach** instance profile from EC2; **delete** test inline policies or **revert** customer-managed policy to previous **default version** in IAM. |
| **Production mistake** | **Attach** previous known-good instance profile; **terminate** bad test instance if needed. |
| **Node already registered in DevNest** (later phase) | **Drain** → **deregister** (internal APIs); then fix IAM; **re-register** or sync after validation. |
| **Secrets never in IAM** | If an access key was mistakenly created on a node user — **delete** IAM user keys; use **instance role only**. |

**Principle:** Prefer **new** IAM policy versions for experiments; keep **v1** as rollback default until Step 3 is validated.

---

## 6. Definition of done (Step 3 only)

- [ ] Execution role trust allows **EC2** only.  
- [ ] **`AmazonSSMManagedInstanceCore`** (or approved substitute) attached.  
- [ ] **Custom** policy grants **S3** only on `arn:aws:s3:::<BUCKET>` + `arn:aws:s3:::<BUCKET>/<PREFIX>*` with **ListBucket** prefix condition.  
- [ ] **ECR** policy present **iff** nodes pull from ECR; repos are **enumerated**, not `*`.  
- [ ] **No** RDS / admin / broad S3 on the execution role without a separate ADR.  
- [ ] **§4** validation commands succeed from a test instance.  
- [ ] Rollback in **§5** documented for operators.

---

## 7. Files touched by this step

| File | Role |
|------|------|
| `docs/PHASE_3B_STEP3_IAM_EXECUTION_NODES.md` | This Step 3 IAM plan and example JSON. |

---

## 8. Related documents

- [PHASE_3B_FLEET_RUNBOOK.md](./PHASE_3B_FLEET_RUNBOOK.md)  
- [PHASE_3B_STEP1_EXECUTION_NODE_EC2.md](./PHASE_3B_STEP1_EXECUTION_NODE_EC2.md)  
- [PHASE_3B_STEP2_SECURITY_GROUPS_NETWORKING.md](./PHASE_3B_STEP2_SECURITY_GROUPS_NETWORKING.md)  
- [Phase 3b Step 4 — Catalog registration (node 2)](./PHASE_3B_STEP4_CATALOG_REGISTRATION_NODE2.md)  

---

*Phase 3b Step 3 — IAM for execution nodes. Documentation only.*
