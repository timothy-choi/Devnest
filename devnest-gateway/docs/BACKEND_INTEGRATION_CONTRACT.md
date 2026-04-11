# Backend ↔ Gateway integration contract (V1)

The **control plane** (DevNest backend) registers workspace routes with the **data plane** via the standalone **route-admin** HTTP API. Traefik reads merged YAML from `traefik/dynamic/` (file provider, `watch: true`).

## V1 route-admin API (implemented)

Base URL: `DEVNEST_GATEWAY_URL` (default `http://127.0.0.1:9080` — host port mapped to route-admin, **not** Traefik’s public `:80` or dashboard `:8080`).

| Method | Path | Body / notes |
|--------|------|----------------|
| `POST` | `/routes` | `{"workspace_id": "<id>", "public_host": "…", "target": "http://…"}` — idempotent upsert |
| `DELETE` | `/routes/{workspace_id}` | Idempotent; `204` |
| `GET` | `/routes` | List registered routes (debug) |
| `GET` | `/health` | Liveness |

Persisted fragment: `traefik/dynamic/100-workspaces.yml` (routers `devnest-reg-{workspace_id}`). TODO: auth, TLS, HA, reconcile.

## Metadata alignment (Workspace / WorkspaceRuntime)

| Backend field | Role | Gateway V1 use |
|---------------|------|----------------|
| `workspace.workspace_id` | Stable integer id | `workspace_id` in API; default `public_host` = `{id}.{DEVNEST_BASE_DOMAIN}` when `public_host` unset |
| `workspace.public_host` | Optional hostname | Sent to route-admin as `public_host` when set |
| `workspace.endpoint_ref` | User-facing URL hint | Unchanged by route-admin in V1 |
| `workspace_runtime.internal_endpoint` | Upstream URL | Normalized to `target` (adds `http://` if missing) |

## Worker behavior (backend)

When `DEVNEST_GATEWAY_ENABLED=true`, after successful **RUNNING** finalization the job worker calls `POST /routes`. On successful **stop** or **delete**, it calls `DELETE /routes/{id}`. Failures are logged only; workspace lifecycle is not rolled back.

## Earlier “internal API on backend” idea

A future option was `POST /internal/gateway/routes` on the backend. V1 uses **direct** backend → route-admin instead to keep the gateway stack standalone. That internal indirection remains a possible later phase.

## Principles

1. **Source of truth:** `WorkspaceRuntime.internal_endpoint` reflects the workspace process URL.
2. **Gateway responsibility:** Map `public_host` → `target` and proxy HTTP/WebSocket (Traefik).
3. **Separation:** Route-admin does not mutate workspace lifecycle; it only applies routes the worker requested.
