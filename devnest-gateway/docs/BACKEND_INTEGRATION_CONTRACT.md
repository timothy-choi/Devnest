# Backend ↔ Gateway integration contract (future)

This document specifies how the **control plane** (DevNest backend) will drive the **data plane** (this gateway). **None of these endpoints exist on the backend yet** — these are the intended V2 contract after a small internal service or sidecar is added.

## Metadata alignment (Workspace / WorkspaceRuntime)

| Backend field | Role | Gateway V1 / future use |
|---------------|------|-------------------------|
| `workspace.workspace_id` | Stable integer id | Derive hostname segment (e.g. `{id}.app.devnest.local`) or custom label from `public_host`. |
| `workspace.public_host` | Optional public hostname hint | Future: use as router `Host()` when set; else derive from `workspace_id` + base domain. |
| `workspace.endpoint_ref` | User-facing / gateway URL hint | Future: populate with `https://{id}.app.devnest.local` (or TLS host) once routes exist. |
| `workspace_runtime.internal_endpoint` | Upstream URL for proxy | **Traefik `loadBalancer.servers[].url`** — e.g. `http://10.0.0.5:8080` or `http://host.docker.internal:9080` in dev. |

V1 today: these values are mirrored **manually** in `traefik/dynamic.yml` for local dev. A later **route-sync** step will read the DB or call internal APIs and update Traefik dynamically.

## Principles

1. **Source of truth:** `WorkspaceRuntime.internal_endpoint` (and related fields) on the backend reflect where the workspace container listens (e.g. `http://10.0.0.5:8080`).
2. **Gateway responsibility:** Map stable **public hostnames** to those upstream URLs and proxy HTTP/WebSocket.
3. **Separation:** The gateway does not mutate workspace lifecycle; it only reflects routes the control plane authorizes.

## Proposed internal API (on the backend)

Base path suggestion: `/internal/gateway` (requires `X-Internal-API-Key`, consistent with existing internal routes).

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/internal/gateway/routes` | Register or replace a route for `workspace_id`. |
| `DELETE` | `/internal/gateway/routes/{workspace_id}` | Deregister route (stop/delete workspace). |
| `GET` | `/internal/gateway/routes` | List registered routes (debug / reconcile). |

### `POST /internal/gateway/routes`

**Request body (JSON):**

```json
{
  "workspace_id": 42,
  "internal_endpoint": "http://10.0.0.5:8080",
  "public_host": "42.app.devnest.local"
}
```

- `workspace_id`: integer primary key (matches subdomain in host-based routing).
- `internal_endpoint`: value compatible with `WorkspaceRuntime.internal_endpoint`.
- `public_host`: optional; if omitted, gateway derives `"{workspace_id}.{DEVNEST_BASE_DOMAIN}"`.

**Response:** `200` with `{ "accepted": true, "route_id": "..." }` or `409` if conflicting.

**Caller:** Worker/orchestrator after successful attach/bring-up, or a dedicated **route-sync** job.  
**TODO:** Define idempotency key and behavior when `internal_endpoint` changes (rolling update).

### `DELETE /internal/gateway/routes/{workspace_id}`

**Response:** `204` or `404` if no route.

**Caller:** Worker after workspace delete or when runtime is torn down.

### `GET /internal/gateway/routes`

**Response:** paginated list of `{ workspace_id, internal_endpoint, public_host, updated_at }` for operators and future reconcile.

## Gateway-side consumption (implementation options)

1. **Route-sync sidecar** (recommended for V2): Poll or subscribe to backend events; update Traefik via **HTTP provider** URL, **Redis** KV, or **file provider** + reload.
2. **Traefik HTTP provider:** Backend exposes a YAML/JSON document Traefik polls — aligns with “dynamic configuration” without writing files on disk.
3. **Manual / ops:** Edit `traefik/dynamic.yml` during early integration (current V1 scaffold).

## Deferred

- Authentication at the gateway (OAuth, JWT validation, session cookies).
- mTLS between gateway and workspace nodes.
- Multi-region or clustered gateway state.
