# Runbook: Disk pressure on an execution node

**Goal:** Avoid new failures on bind mounts / snapshot export and keep the fleet stable.

## Signals

- Heartbeat payload **`disk_free_mb`** low in **`metadata_json.heartbeat`** (inspect via list API / DB).
- Workspace jobs fail with orchestrator / Docker “no space” class errors on that host only.

## Immediate actions

1. **List** capacity + heartbeat metadata:
   ```bash
   ./scripts/devnest_ops_nodes.sh list
   ```

2. **Drain** the node (stops **new** placements): [Drain runbook](./RUNBOOK_EXECUTION_NODE_DRAIN.md).

3. **On the host:** prune old workspace project dirs (ops policy), expand EBS volume, or migrate workloads after **stop/delete** from the UI/API.

## Placement note

Control-plane **`allocatable_disk_mb`** minus runtime reservations drives **scheduling**; a full root volume may still fail **bring-up** or **snapshot export** even when placement allowed the node. Treat disk as **host SRE** + **drain** workflow.

## Verify

- Heartbeat **`disk_free_mb`** rises after cleanup.
- [Undrain](./RUNBOOK_EXECUTION_NODE_UNDRAIN.md) when safe.
