# Workspace node registry (Phase 1)

This document describes **Phase 1** of DevNest’s multi-node preparation: explicit association between workspaces and a **node registry** row, without changing Docker placement, Traefik, or adding a second machine.

## Canonical model

- **Registry table:** `execution_node` (SQLAlchemy / SQLModel: `ExecutionNode`). Operators and product docs may call this the **workspace node registry**; the table name is unchanged for backwards compatibility.
- **Placement string:** `workspace_runtime.node_id` continues to store the **`node_key`** string used by the orchestrator and Docker binding (same meaning as historical `DEVNEST_NODE_ID`).
- **Control-plane FK:** `workspace.execution_node_id` references `execution_node.id` (integer PK). It is set when a workspace is **created** (default local bootstrap node) and kept aligned when a job reaches **RUNNING** (from the chosen `node_key`).

## Fields (registry)

The `execution_node` row already carries the information Phase 1 needs:

| Concept | Column / behaviour |
|--------|---------------------|
| Stable id | `id` (PK) |
| Human name | `name` |
| Host hints | `hostname`, `private_ip`, `public_ip` |
| Slot ceiling | `max_workspaces` |
| Status | `status` (`READY`, `DRAINING`, `NOT_READY`, …) and `schedulable` |
| Heartbeat | `last_heartbeat_at` (optional; future agents) |

Mapping to informal “healthy / draining / unavailable”: **`READY` + `schedulable`** ≈ healthy; **`DRAINING`** ≈ draining; other statuses or `schedulable=false` ≈ unavailable for new placement.

## Bootstrap

On API/worker startup, `ensure_default_local_execution_node()` **idempotently** ensures exactly **one registry row per configured `node_key`** (from `DEVNEST_NODE_ID`, default `node-1`). Re-running startup or migrations updates that row in place; it does not create duplicate rows for the same key. This is the **default node** for single-host Compose / one EC2 today.

## Migrations

Revision **`0011_workspace_execution_node_fk`**:

1. Calls the same `ensure_default_local_execution_node()` logic used at runtime so a fresh `alembic upgrade` database always has the default row **before** workspace FK backfill (even if `init_db` has not run yet).
2. Adds `workspace.execution_node_id` (nullable), index, and FK to `execution_node.id`.
3. Backfills from `workspace_runtime.node_id` = `execution_node.node_key` where a match exists.
4. Sets any remaining NULLs to the **bootstrap default node’s id** (same row as step 1), not an arbitrary `MIN(id)`, so multi-node RDS clusters do not mis-assign orphans to the wrong node.
5. Sets **NOT NULL** (skipped if the column is already NOT NULL from a retried migration).

## Internal API (capacity listing)

`GET /internal/execution-nodes/` (scoped `X-Internal-API-Key` for **infrastructure**) returns each node plus **`active_workspace_slots`** and **`available_workspace_slots**`, using the same capacity cohort as placement (`count_active_workloads_on_node_key`). Responses intentionally **omit** `metadata_json` and SSH-related columns so operator JSON does not carry opaque config blobs or connection secrets.

## Later phases

Phase 2+ will introduce real multi-node scheduling, routing by node, and optional shared storage. Phase 1 only adds **durable registry semantics** and observability so those features can build on stable FKs and APIs.
