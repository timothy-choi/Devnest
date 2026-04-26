# Runbook: Failed or stuck workspace on one execution node

**Goal:** Clear user-visible **ERROR**, free capacity slots, and stale gateway routes without assuming a specific host name.

## Identify the node

1. **By workspace:**
   - `GET /workspaces/{id}` (or DB): **`execution_node_id`**, runtime **`node_id`** / **`container_id`**.
2. **By fleet:**
   - `./scripts/devnest_ops_nodes.sh workspaces` — groups by **`workspace_runtime.node_id`**.

## User / control-plane actions

1. **Stop** or **delete** the workspace from the product UI/API (queues **STOP** / **DELETE** jobs).
2. If status is inconsistent, use **reconcile runtime** if your deployment exposes **`POST .../reconcile-runtime`** (internal or product).

## Gateway

- Stop/delete finalization **deregisters** Traefik/route-admin routes best-effort.
- Logs: **`gateway.route.deregistered`**, **`reconcile.*`** as applicable.

## Node agent / Docker

- If the container is wedged but API thinks RUNNING, use **smoke** or host access:
  ```bash
  ./scripts/devnest_ops_nodes.sh smoke '{"node_key":"<NODE_KEY>","read_only_command":"docker_ps"}'
  ```
- EC2 **SSM/SSH** issues: fix connectivity before blaming placement.

## Capacity

- **ERROR** / **DELETED** / **STOPPED** workloads do not count toward active slot pressure the same way as **RUNNING**; if a ledger row is stale, **reconcile** may repair.

## Escalation

- [Drain](./RUNBOOK_EXECUTION_NODE_DRAIN.md) the host if many workspaces fail with the same disk/Docker symptom; see [Disk full](./RUNBOOK_DISK_FULL_EXECUTION_NODE.md).
