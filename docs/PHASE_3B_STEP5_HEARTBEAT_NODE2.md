# Phase 3b Step 5 — Heartbeat from node 2 while unschedulable (documentation only)

**Status:** Operator runbook. **No** application code changes, **no** route-admin or Traefik changes, **no** remote Docker/SSM **orchestration** for workspace jobs (only local `docker` checks on the node for heartbeat telemetry).

**Prerequisites:** [Step 4 — Catalog registration](./PHASE_3B_STEP4_CATALOG_REGISTRATION_NODE2.md) (node 2 in `execution_node` with **`schedulable=false`**), [Step 2 — Networking](./PHASE_3B_STEP2_SECURITY_GROUPS_NETWORKING.md) (node → API egress), [Step 3 — IAM](./PHASE_3B_STEP3_IAM_EXECUTION_NODES.md) (optional; heartbeat uses **internal API key**, not instance profile, unless you proxy via agent role).

**Goal:** Node 2 periodically **`POST`s** `/internal/execution-nodes/heartbeat` with **`node_key`** for node 2 and payload fields **`docker_ok`**, **`disk_free_mb`**, **`slots_in_use`**, **`version`**, so **`last_heartbeat_at`** and **`metadata_json.heartbeat`** stay fresh — while **`schedulable` remains `false`** so **placement stays on node 1** (and any other schedulable nodes).

---

## 1. API contract (current backend)

- **Path:** `POST /internal/execution-nodes/heartbeat`  
- **Header:** `X-Internal-API-Key: <infrastructure-scoped key>` (same family as other `/internal/execution-nodes/*` routes). **Do not** commit keys; load from SSM Parameter Store, instance user-data secrets pattern, or root-only env file on the node.  
- **JSON body** (`ExecutionNodeHeartbeatInBody` in `internal_execution_nodes.py`):

| Field | Type | Notes |
|--------|------|--------|
| `node_key` | string | e.g. `node-2` |
| `docker_ok` | bool | default `true` if omitted |
| `disk_free_mb` | int or null | optional; include for richer telemetry |
| `slots_in_use` | int or null | optional; use `0` until workloads run on this node |
| `version` | string or null | e.g. `phase3b-node2-heartbeat-1` |

**Placement:** The handler **does not** set `schedulable`. Placement still requires **`schedulable=true`** and **`status=READY`** in `_schedulable_base_predicates`, so **node 2 with `schedulable=false` never receives new workspaces** from this heartbeat alone.

**Status / READY:** The current heartbeat handler **does not** promote `status` to `READY` (it updates `last_heartbeat_at`, `metadata_json.heartbeat`, and docker-related error fields only). You may leave **`status`** as set at registration (often `READY` when the instance was running). **`READY` + `schedulable=false`** remains **non-schedulable**. If you prefer **`NOT_READY`** in the catalog until a later step, set that via SQL or lifecycle tools **independently** of heartbeat.

---

## 2. Node 2 heartbeat setup

### 2.1 Secrets and URL on the instance

| Variable | Meaning |
|----------|---------|
| `DEVNEST_INTERNAL_API_BASE_URL` | Base URL **reachable from node 2** to the control plane (private), e.g. `https://api.internal:8443` — **no** trailing slash required. |
| `DEVNEST_INTERNAL_API_KEY` | Infrastructure-scoped internal API key (root-only file `0400`, or SSM `GetParameter` with decryption in the script). |

Full heartbeat URL: `{BASE}/internal/execution-nodes/heartbeat`.

### 2.2 Collecting metrics (simple shell)

Example values for the JSON body (adjust paths to match **`WORKSPACE_PROJECTS_BASE`** on node 2, e.g. `/var/lib/devnest/workspace-projects`):

- **`docker_ok`:** `docker info >/dev/null 2>&1` exit code.  
- **`disk_free_mb`:** `df -PB1M --output=avail <PROJECT_BASE> | tail -1` (parse integer).  
- **`slots_in_use`:** `0` until this node runs workspace containers; optional later: count running devnest-related containers.  
- **`version`:** static string or `uname -n`-suffixed tag for traceability.

### 2.3 One-shot `curl` (manual test)

```bash
curl -sS -o /tmp/hb.out -w "%{http_code}" \
  -X POST "${DEVNEST_INTERNAL_API_BASE_URL}/internal/execution-nodes/heartbeat" \
  -H "Content-Type: application/json" \
  -H "X-Internal-API-Key: ${DEVNEST_INTERNAL_API_KEY}" \
  -d "$(jq -n \
      --arg nk "node-2" \
      --argjson docker_ok true \
      --argjson disk "$(df -PB1M --output=avail /var/lib/devnest/workspace-projects 2>/dev/null | tail -1 | tr -d ' ')" \
      --argjson slots 0 \
      --arg ver "phase3b-node2-heartbeat-manual" \
      '{node_key:$nk, docker_ok:$docker_ok, disk_free_mb:($disk|tonumber?), slots_in_use:$slots, version:$ver}')" \
| tail -c 3
```

**Expect:** HTTP **200** and JSON with `node_key`, `last_heartbeat_at` non-null. If `disk_free_mb` parsing fails, omit it or send a literal integer.

### 2.4 Systemd timer (recommended)

Install two units under `/etc/systemd/system/` on node 2 (paths illustrative).

**`/etc/systemd/system/devnest-node-heartbeat.service`** (oneshot):

```ini
[Unit]
Description=DevNest execution node heartbeat POST
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=-/etc/devnest/heartbeat.env
ExecStart=/usr/local/bin/devnest-node-heartbeat.sh
```

**`/etc/systemd/system/devnest-node-heartbeat.timer`**:

```ini
[Unit]
Description=Run DevNest node heartbeat every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
Persistent=true

[Install]
WantedBy=timers.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now devnest-node-heartbeat.timer
```

**`/etc/devnest/heartbeat.env`** (mode `0600`, owned by root):

```bash
DEVNEST_INTERNAL_API_BASE_URL=https://api.example.internal
DEVNEST_INTERNAL_API_KEY=REPLACE_VIA_SSM_OR_VAULT
NODE_KEY=node-2
HEARTBEAT_VERSION=phase3b-node2-systemd-1
PROJECT_BASE=/var/lib/devnest/workspace-projects
```

**`/usr/local/bin/devnest-node-heartbeat.sh`** (mode `0755`, root):

```bash
#!/bin/bash
set -euo pipefail
: "${DEVNEST_INTERNAL_API_BASE_URL:?}" "${DEVNEST_INTERNAL_API_KEY:?}" "${NODE_KEY:?}"
BASE="${DEVNEST_INTERNAL_API_BASE_URL%/}"
PB="${PROJECT_BASE:-/var/lib/devnest/workspace-projects}"
DISK_MB=0
if [[ -d "$PB" ]]; then
  DISK_MB="$(df -PB1M --output=avail "$PB" 2>/dev/null | tail -1 | tr -dc '0-9' || echo 0)"
fi
DOCKER_OK=true
if ! docker info >/dev/null 2>&1; then DOCKER_OK=false; fi
VER="${HEARTBEAT_VERSION:-node-heartbeat}"
# JSON body: all fields the API accepts for rich telemetry (disk/slots/version optional in schema).
BODY=$(printf '%s' "{\"node_key\":\"${NODE_KEY}\",\"docker_ok\":${DOCKER_OK},\"disk_free_mb\":${DISK_MB},\"slots_in_use\":0,\"version\":\"${VER}\"}")
code=$(curl -sS -o /tmp/devnest-hb-last.json -w "%{http_code}" \
  -X POST "${BASE}/internal/execution-nodes/heartbeat" \
  -H "Content-Type: application/json" \
  -H "X-Internal-API-Key: ${DEVNEST_INTERNAL_API_KEY}" \
  -d "$BODY")
echo "$code"
test "$code" -ge 200 && test "$code" -lt 300
```

**Note:** `printf` into JSON assumes `NODE_KEY` and `VER` are safe (no `"` or backslashes); use **`jq -n`** in production if values can contain special characters.

### 2.5 Cron alternative

```cron
* * * * * root /usr/local/bin/devnest-node-heartbeat.sh >>/var/log/devnest-heartbeat.log 2>&1
```

Prefer **systemd timer** for journald integration and `OnBootSec`.

---

## 3. Validation commands

Replace `node-2`, host, and key injection as appropriate.

### 3.1 `last_heartbeat_at` advances (SQL)

```sql
SELECT node_key, status, schedulable, last_heartbeat_at, updated_at
FROM execution_node
WHERE node_key = 'node-2';
```

Wait one timer interval; re-run. **Expect:** `last_heartbeat_at` increases.

### 3.2 `metadata_json.heartbeat` updates (PostgreSQL example)

```sql
SELECT node_key,
       metadata_json->'heartbeat' AS heartbeat
FROM execution_node
WHERE node_key = 'node-2';
```

**Expect:** Keys such as `received_at`, `docker_ok`, optional `disk_free_mb`, `slots_in_use`, `version`.

### 3.3 `schedulable` stays false

```sql
SELECT node_key, schedulable FROM execution_node WHERE node_key = 'node-2';
```

**Expect:** **`schedulable = false`**.

### 3.4 New workspaces still land on node 1

After creating a test workspace:

```sql
SELECT w.workspace_id, w.execution_node_id, en.node_key
FROM workspace w
LEFT JOIN execution_node en ON en.id = w.execution_node_id
ORDER BY w.workspace_id DESC
LIMIT 3;
```

**Expect:** Latest rows **`node_key` ≠ `node-2`** while node 2 remains non-schedulable.

### 3.5 API list (optional)

```bash
curl -sS -H "X-Internal-API-Key: <KEY>" "https://<API>/internal/execution-nodes/" | jq '.[] | select(.node_key=="node-2") | {node_key, schedulable, last_heartbeat_at}'
```

---

## 4. Troubleshooting

| Symptom | Likely cause | What to check |
|---------|----------------|---------------|
| **HTTP 000 / connection refused** | Bad **`DEVNEST_INTERNAL_API_BASE_URL`**, wrong port, or TLS name mismatch | From node: `curl -v "${DEVNEST_INTERNAL_API_BASE_URL}/ready"` (or `/docs` if enabled); DNS `getent hosts`; SG egress from **execution node** to API (Step 2). |
| **HTTP 401** | Bad or missing **`X-Internal-API-Key`**; wrong scope | Use same key family as `register-existing`; confirm header name spelling. |
| **HTTP 403** | Key valid but not **infrastructure** scope | Rotate to correct scoped secret per internal auth docs. |
| **HTTP 404** `execution node key=… not found` | Typo in **`node_key`** or row missing | `SELECT node_key FROM execution_node`; re-run Step 4 registration. |
| **Docker always false** | Docker daemon down, wrong socket permissions, disk full | On node: `sudo systemctl status docker`, `sudo docker info`. |
| **`disk_free_mb` wrong / jq errors** | Path missing or `df` parse | Ensure **`PROJECT_BASE`** exists (`Step 1`); fix script parsing; omit optional field temporarily. |
| **Timer never runs** | Timer not enabled, clock skew | `systemctl list-timers | grep devnest`, `timedatectl`. |

---

## 5. Rollback steps

| Step | Action |
|------|--------|
| 1 | **Stop** heartbeat automation: `sudo systemctl disable --now devnest-node-heartbeat.timer` or remove cron entry. |
| 2 | Remove `/usr/local/bin/devnest-node-heartbeat.sh` and unit files if decommissioning. |
| 3 | If node 2 should leave the fleet: **`POST /internal/execution-nodes/drain`** then **`deregister`** (see Step 4 / fleet runbook). |
| 4 | **Revoke** or rotate **`DEVNEST_INTERNAL_API_KEY`** if it was exposed in logs. |

Stopping heartbeat **does not** auto-set `schedulable=true`; placement policy unchanged.

---

## 6. Definition of done (Step 5 only)

- [ ] Node 2 emits successful heartbeats on an interval (systemd timer or cron).  
- [ ] **`last_heartbeat_at`** and **`metadata_json.heartbeat`** update on the DB row.  
- [ ] **`schedulable`** remains **`false`**.  
- [ ] New workspaces still bind to **node 1** (or other schedulable nodes).  
- [ ] No route-admin, Traefik, or user-facing app behavior changes for this step.

---

## 7. Files touched by this step

| File | Role |
|------|------|
| `docs/PHASE_3B_STEP5_HEARTBEAT_NODE2.md` | This Step 5 heartbeat runbook. |

---

## 8. Related documents

- [Phase 3b Step 4 — Catalog registration](./PHASE_3B_STEP4_CATALOG_REGISTRATION_NODE2.md)  
- [Phase 3b Step 2 — Security groups and networking](./PHASE_3B_STEP2_SECURITY_GROUPS_NETWORKING.md)  
- [Phase 3b Fleet runbook](./PHASE_3B_FLEET_RUNBOOK.md)  

---

*Phase 3b Step 5 — node 2 heartbeat while unschedulable. Documentation only.*
