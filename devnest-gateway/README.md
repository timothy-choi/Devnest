# DevNest Gateway (Data Plane)

Standalone **reverse proxy / edge router** for DevNest. It terminates user-facing HTTP(S) hostnames and forwards traffic to **workspace runtimes** using upstream URLs that originate from the control plane (`WorkspaceRuntime.internal_endpoint`).

The **DevNest backend** (control plane) lives in `../backend/` and is **not** modified by this service.

---

## 1. Technology choice: Traefik (V1)

| Option | Verdict |
|--------|---------|
| **Traefik** | **Selected.** Native Docker labels, file & HTTP providers, automatic WebSocket upgrades, dashboard for local debugging, widely used at the edge. Fits dynamic route add/remove without hand-writing NGINX `upstream` blocks. |
| NGINX | Excellent performance and familiarity; dynamic upstreams often need OpenResty/lua, NJS, or external template generation — more glue for “register route per workspace.” |
| Envoy | Powerful for large-scale mesh/ingress; heavier operational footprint for a minimal V1. |
| Custom FastAPI proxy | Fine for prototypes; you re-implement connection pooling, WebSockets, timeouts, and observability — defer unless needed. |

---

## 2. Architecture (text diagram)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CONTROL PLANE (backend)                          │
│  Workspace Service → WorkspaceJob → Orchestrator → WorkspaceRuntime      │
│  internal_endpoint: http://<ip>:<port>                                  │
│  (Future: POST /internal/gateway/routes — see docs/)                     │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │  register / revoke routes (future)
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      DATA PLANE (this repo: Traefik)                     │
│  Host: <workspace_id>.app.devnest.local                                  │
│  Proxy: HTTP + WebSocket → internal_endpoint                             │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
                                ▼
                    Workspace runtime (code-server, etc.)
```

**Responsibility split**

| Layer | Owns |
|-------|------|
| Control plane | Lifecycle, jobs, orchestration, `Workspace` / `WorkspaceRuntime` / `WorkspaceJob`, SSE, attach/access **metadata**. |
| Data plane (gateway) | Hostname → upstream mapping, TLS termination (later), L7 proxy, connection handling to runtimes. |

---

## 3. Directory layout

```
devnest-gateway/
├── docker-compose.yml       # Traefik + mock upstream for local dev
├── traefik/
│   ├── traefik.yml          # Static config (entrypoints, providers, logging)
│   └── dynamic.yml          # Routers/services (edit or replace via sync later)
├── config/
│   └── .env.example         # Environment variable documentation
├── scripts/
│   └── hosts-snippet.sh     # Optional /etc/hosts hints
├── docs/
│   └── BACKEND_INTEGRATION_CONTRACT.md
└── README.md
```

---

## 4. Routing model

- **Host-based routing:** `{workspace_id}.{DEVNEST_BASE_DOMAIN}`  
  Example: `42.app.devnest.local` → upstream from `WorkspaceRuntime.internal_endpoint` (e.g. `http://10.0.0.5:8080`).
- **WebSocket:** Traefik forwards `Connection: Upgrade` by default for HTTP services.
- **V1:** Routes are declared in `traefik/dynamic.yml` (file provider, `watch: true`). Editing the file applies changes without rebuilding images.

---

## 5. Local development

### Prerequisites

- Docker + Docker Compose v2
- Optional: copy `config/.env.example` to `.env` next to `docker-compose.yml` and adjust ports

### Hostname resolution

Map hostnames to the machine running Traefik (usually `127.0.0.1`):

```bash
chmod +x scripts/hosts-snippet.sh
./scripts/hosts-snippet.sh
# Paste suggested lines into /etc/hosts (macOS/Linux)
```

### Start the stack

```bash
cd devnest-gateway
docker compose up -d
```

### Smoke test

- Mock route: `curl -s -H "Host: 1.app.devnest.local" http://127.0.0.1/`  
  Expect `whoami`-style output from `mock-upstream`.
- Dashboard (dev only): `http://127.0.0.1:8080/dashboard/` (Traefik API insecure — **TODO:** lock down for production).

### Point at a real workspace on the host

1. Run your workspace HTTP server on a host port (e.g. `9080`).
2. Add a router + service in `traefik/dynamic.yml` using `http://host.docker.internal:9080` (already allowed in `docker-compose.yml` via `extra_hosts`).
3. Add matching `/etc/hosts` entry for `{id}.app.devnest.local`.

The DevNest API remains on its own port/hostname (e.g. `api.devnest.local:8000`) — **not** served by this compose file.

---

## 6. Configuration (environment variables)

| Variable | Default | Purpose |
|----------|---------|---------|
| `DEVNEST_BASE_DOMAIN` | `app.devnest.local` | Suffix for workspace hosts (documentation + future templating). |
| `DEVNEST_GATEWAY_PORT` | `80` | Host port mapped to Traefik `web`. |
| `DEVNEST_DASHBOARD_PORT` | `8080` | Traefik dashboard (dev). |
| `DEVNEST_GATEWAY_TLS_ENABLED` | _(not used yet)_ | **TODO:** enable `websecure` + certs. |

---

## 7. Backend integration (future)

See [`docs/BACKEND_INTEGRATION_CONTRACT.md`](docs/BACKEND_INTEGRATION_CONTRACT.md) for the proposed `POST/DELETE/GET /internal/gateway/routes` contract. **The backend does not implement these yet** by design.

---

## 8. Roadmap

| Phase | Items |
|-------|--------|
| **V1 (this scaffold)** | Traefik file provider, Compose, host routing, WebSocket passthrough, docs. |
| **V2** | Internal backend routes + route-sync sidecar or Traefik HTTP provider; remove manual `dynamic.yml` edits. |
| **V3** | TLS termination, real certificates (ACME) once DNS exists. |
| **V4** | HA gateway (Redis/consul provider), multi-node, health-based load balancing. |
| **Later** | K8s Ingress, AWS ALB/NLB, EC2/node agent integration, auth at edge, reconcile with `WorkspaceRuntime`. |

---

## 9. Alignment with DevNest documentation

- **Control plane** (`/workspaces`, jobs, orchestrator, SSE) stays in `backend/`.
- **Data plane** (`/ws`-style user traffic in docs) is satisfied here by **hostname → runtime** proxying.
- **Gateway URL** fields that are `null` in the API today can later point at `https://{id}.{DEVNEST_BASE_DOMAIN}` once TLS and registration exist.

---

## License

Follow the root DevNest repository license.
