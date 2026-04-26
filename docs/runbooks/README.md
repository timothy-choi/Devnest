# DevNest execution fleet — operator runbooks

Concise procedures for **multi-node** workspace execution nodes (catalog + placement + worker).  
All internal HTTP calls use **`X-Internal-API-Key`** with **infrastructure** scope.

**Index**

| Runbook | When to use |
|---------|----------------|
| [Drain node](./RUNBOOK_EXECUTION_NODE_DRAIN.md) | Stop **new** placements on a host; running workspaces stay. |
| [Undrain node](./RUNBOOK_EXECUTION_NODE_UNDRAIN.md) | Return a drained or catalog-disabled node to the schedulable pool. |
| [Deregister node](./RUNBOOK_EXECUTION_NODE_DEREGISTER.md) | Soft-remove catalog row (**TERMINATED**); does not stop EC2. |
| [Stale heartbeat](./RUNBOOK_STALE_EXECUTION_NODE_HEARTBEAT.md) | Placement skips nodes or ops sees old `last_heartbeat_at`. |
| [Disk-full node](./RUNBOOK_DISK_FULL_EXECUTION_NODE.md) | Low `disk_free_mb` in heartbeat or disk pressure on instance. |
| [Failed workspace on node](./RUNBOOK_FAILED_WORKSPACE_ON_EXECUTION_NODE.md) | ERROR/ stuck workload on one execution host. |

**Tooling**

- **HTTP:** `GET /internal/execution-nodes/`, `GET /internal/execution-nodes/workspaces-by-node`, `POST /drain`, `POST /undrain`, `POST /deregister`, `POST /heartbeat`, `POST /smoke-read-only` — see [Phase 3b Step 12 — Ops hardening](../PHASE_3B_STEP12_OPS_HARDENING.md).
- **CLI helper:** `scripts/devnest_ops_nodes.sh` (`list`, `workspaces`, `drain`, `undrain`, `heartbeat`, `smoke`, `deregister`).

**Control-plane overview:** [Phase 3b fleet runbook](../PHASE_3B_FLEET_RUNBOOK.md).
