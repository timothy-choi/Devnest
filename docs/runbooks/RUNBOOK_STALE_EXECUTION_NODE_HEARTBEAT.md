# Runbook: Stale or missing execution node heartbeat

**Goal:** Restore **`last_heartbeat_at`** so ops visibility is correct and, when **`DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT=true`**, placement can select the node again.

## Symptoms

- **`GET /internal/execution-nodes/`** shows large **`heartbeat_age_seconds`** or **null** (never beat).
- **`placement.no_schedulable_node`** in logs with text about heartbeat gate.
- Pinned operator CREATE fails validation for “fresh heartbeat”.

## Diagnosis

1. List nodes + age:
   ```bash
   ./scripts/devnest_ops_nodes.sh list
   ```
2. Confirm worker/agent config:
   - **`DEVNEST_NODE_HEARTBEAT_ENABLED=true`** on **workspace-worker** with **`INTERNAL_API_BASE_URL`** + key, **or**
   - Cron/systemd on the execution host posting **`POST /internal/execution-nodes/heartbeat`**.

3. Control-plane logs: **`execution_node_heartbeat_received`**, **`execution_node_heartbeat_node_updated`**, or **`execution_node_heartbeat_unknown_node`** (wrong **`node_key`**).

## Remediation

1. Fix URL/key/network so POSTs succeed.
2. Optionally widen **`DEVNEST_NODE_HEARTBEAT_MAX_AGE_SECONDS`** temporarily (then tighten).
3. If gating was enabled too early, set **`DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT=false`** until fleet is stable (see [Execution node heartbeat](../EXECUTION_NODE_HEARTBEAT.md)).

4. **Manual poke** (replace body with real metrics):
   ```bash
   ./scripts/devnest_ops_nodes.sh heartbeat '{"node_key":"<NODE_KEY>","docker_ok":true,"disk_free_mb":100000,"slots_in_use":0,"version":"manual-recovery"}'
   ```

## Verify

- **`heartbeat_age_seconds`** drops on the next list call.
- Placement test: enqueue a CREATE and check **`scheduler.node.selected`** includes **`target_node_heartbeat_age_seconds`**.
