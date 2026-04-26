# Execution node heartbeat (Phase 3a)

Phase 3a adds **control-plane liveness** for execution nodes: the API records periodic heartbeats on `execution_node.last_heartbeat_at` and optional detail under `metadata_json["heartbeat"]`. It does **not** add a second EC2, change Traefik, or change how workspace containers are placed on disk beyond an **optional** scheduler gate (off by default).

## What the heartbeat means

- **Who sends it (integration / recommended):** When `DEVNEST_NODE_HEARTBEAT_ENABLED=true`, `workspace-worker` starts a **daemon thread** (`execution_node_heartbeat_emitter`) that immediately POSTs and then repeats every `DEVNEST_NODE_HEARTBEAT_INTERVAL_SECONDS` to `{INTERNAL_API_BASE_URL}/internal/execution-nodes/heartbeat` with `X-Internal-API-Key` (`INTERNAL_API_KEY` or `INTERNAL_API_KEY_INFRASTRUCTURE`). Logs: `execution_node_heartbeat_emitter_started`, `execution_node_heartbeat_success`, `execution_node_heartbeat_failure`, or `execution_node_heartbeat_emitter_misconfigured` if URL/key is missing.
- **Who sends it (fallback):** If the dedicated emitter is **off**, the worker may still call `emit_default_local_execution_node_heartbeat` after each job poll tick (and optionally via `DEVNEST_WORKER_HEARTBEAT_INTERNAL_API_BASE_URL` for HTTP from that path, or direct DB writes). The in-process API worker tick uses the same helper when `DEVNEST_WORKER_ENABLED=true`. Operators or agents can also call the internal HTTP API directly.
- **What it measures (embedded emitter):** Docker reachability (`docker_ok`), free disk MB under `WORKSPACE_PROJECTS_BASE` (fallback: temp dir), active workload slot count for the default `node_key`, and an emitter `version` string.
- **What is stored:** `last_heartbeat_at` (UTC), merged `metadata_json["heartbeat"]` (last snapshot: `docker_ok`, `disk_free_mb`, `slots_in_use`, `version`, `received_at`). If `docker_ok` is false, `last_error_code` / `last_error_message` are set to `DOCKER_UNREACHABLE` / a short explanation; they are cleared when `docker_ok` is true again. When `docker_ok` is true, **`NOT_READY`** may be promoted to **`READY`** for schedulable nodes that are not draining / terminating / provisioning; **`DRAINING`** and **`ERROR`** are not overridden by heartbeat.

## Internal API

**Registered path:** `POST /internal/execution-nodes/heartbeat` (full URL from inside Compose: `http://backend:8000/internal/execution-nodes/heartbeat`). Same **`X-Internal-API-Key`** scoping as other infrastructure routes (`InternalApiScope.INFRASTRUCTURE` / legacy `INTERNAL_API_KEY`). On API startup, logs include `devnest_phase3a_execution_node_heartbeat_route_registered` with the resolved path and HTTP methods.

**If you see HTTP 404:** read the JSON `detail`. Unknown **`node_key`** (not the default local key) returns 404. Wrong URL (e.g. doubled path) returns a framework 404 — set `INTERNAL_API_BASE_URL` to `http://backend:8000` only; the worker client normalizes a trailing `/internal/execution-nodes` suffix.

### Verifying route registration (must show `POST`)

From the **backend** repo root, with the same Python env as the API:

```bash
cd backend && python3 -c "
from app.main import app
p = '/internal/execution-nodes/heartbeat'
assert p in app.openapi()['paths'], f'missing {p} in OpenAPI paths'
assert 'post' in app.openapi()['paths'][p], f'missing POST on {p}'
print('OK:', sorted(app.openapi()['paths'][p].keys()), p)
"
```

You should see `post` listed for `/internal/execution-nodes/heartbeat`. If an older image omitted the route from `internal_execution_nodes.py`, the API still registers it via **`main.py` fallback** and logs `execution_node_heartbeat_route_registered_via_main_fallback` once at import.

JSON body:

| Field | Type | Notes |
|-------|------|--------|
| `node_key` | string | Required |
| `docker_ok` | bool | Required |
| `disk_free_mb` | int | ≥ 0 |
| `slots_in_use` | int | ≥ 0 (stored in metadata) |
| `version` | string | 1–128 chars |

Response: `id`, `node_key`, `status`, `schedulable`, `last_heartbeat_at` (heartbeat metadata remains in DB `metadata_json`; query SQL for full snapshot).

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DEVNEST_WORKER_EMIT_EXECUTION_NODE_HEARTBEAT` | `true` | If false, the embedded worker emitter skips writes (internal POST still works). |
| `DEVNEST_EXECUTION_NODE_HEARTBEAT_EMITTER_VERSION` | `worker-embedded` | String stored in heartbeat metadata as `version` for the default local emitter. |
| `DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT` | `false` | If true, **new placement** requires `last_heartbeat_at` within max age. |
| `DEVNEST_NODE_HEARTBEAT_MAX_AGE_SECONDS` | `300` | Freshness window when gating is on (coerced 30–86400). |
| `DEVNEST_NODE_HEARTBEAT_ENABLED` | `false` | When `true`, workspace-worker runs the **interval HTTP** heartbeat emitter (integration Compose sets `true`). |
| `INTERNAL_API_BASE_URL` | *(empty)* | FastAPI base URL reachable from the worker (e.g. `http://backend:8000`). Required for the dedicated emitter. |
| `DEVNEST_NODE_KEY` | *(empty)* | `node_key` sent in the heartbeat body; empty uses `DEVNEST_NODE_ID` / `node-1`. |
| `DEVNEST_NODE_HEARTBEAT_INTERVAL_SECONDS` | `30` | Sleep between POSTs (coerced 5–3600). |
| `DEVNEST_WORKER_HEARTBEAT_INTERNAL_API_BASE_URL` | *(empty)* | Legacy: when the dedicated emitter is **off**, non-empty value makes the **per-tick** emit path POST to this base URL instead of writing SQL directly. |

Environment names follow **pydantic-settings** convention: uppercase, derived from the `Settings` field names in `backend/app/libs/common/config.py` (e.g. `devnest_require_fresh_node_heartbeat` → `DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT`).

## Verifying `last_heartbeat_at`

**SQL (Postgres example):**

```sql
SELECT id, node_key, last_heartbeat_at, last_error_code,
       metadata_json->'heartbeat' AS heartbeat
FROM execution_node
ORDER BY id;
```

**Logs:**

- Fresh: `execution_node_heartbeat_fresh` (API `init_db` after bootstrap).
- Missing or too old: `execution_node_heartbeat_stale_or_missing`.
- Dedicated emitter: `execution_node_heartbeat_emitter_started`, then `execution_node_heartbeat_success` or `execution_node_heartbeat_failure` each interval.
- Fallback per-tick path: `execution_node_heartbeat_emitted` (DB) or `execution_node_heartbeat_emitted_via_http` (HTTP via `DEVNEST_WORKER_HEARTBEAT_INTERNAL_API_BASE_URL`).

**Deploy script (non-fatal):** `./scripts/deploy_integration.sh` warns if recent `workspace-worker` logs do not yet show any of the `execution_node_heartbeat_*` lines above.

## Troubleshooting stale heartbeat

1. **Confirm the worker is running** and `DEVNEST_NODE_HEARTBEAT_ENABLED=true` with **`INTERNAL_API_BASE_URL`** and **`INTERNAL_API_KEY`** (or infrastructure-scoped key) set in the **workspace-worker** container env (Compose `environment:` overrides `env_file` for the same keys).
2. **Check logs** for `execution_node_heartbeat_emitter_misconfigured` (missing URL/key) or `execution_node_heartbeat_failure` (HTTP status / network). If the dedicated emitter is off, check `DEVNEST_WORKER_EMIT_EXECUTION_NODE_HEARTBEAT` is not `false` unintentionally.
3. **If `DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT=true`**, new workspace **creation** can return **503** when no node has a recent `last_heartbeat_at`. Fix the emitter or temporarily disable the flag.
4. **`docker_ok=false`:** Engine unreachable from the worker environment; fix Docker socket/mounts; error fields explain the last failed heartbeat.
5. **503 on workspace create with gating on:** Logs / `NoSchedulableNodeError` may include a sentence about the heartbeat gate; confirm at least one node has `last_heartbeat_at` newer than `DEVNEST_NODE_HEARTBEAT_MAX_AGE_SECONDS`, or relax the flag.

## Scheduler gating (optional)

With **`DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT=true`**, `select_node_for_workspace` adds SQL predicates so only nodes with `last_heartbeat_at >= now - DEVNEST_NODE_HEARTBEAT_MAX_AGE_SECONDS` are candidates. Default **`false`** preserves pre–Phase 3a behavior (null or old heartbeats do not block placement).
