# DevNest Gateway (V1 — Traefik)

Standalone **data-plane** reverse proxy for workspace HTTP/WebSocket traffic. It is **not** the backend control plane (`/workspaces`, etc.). Control plane stays in `backend/`; this service only routes by hostname (and optional path) to upstreams.

## What works in V1

- Docker Compose runs **Traefik** (public **80**, dashboard **8080**), **route-admin** (registration API on host **9080** by default), and **mock-upstream**.
- Traefik **file provider** loads **`traefik/dynamic/*.yml`** (merged, `watch: true`).
- **route-admin** implements `POST/DELETE/GET /routes` and rewrites `traefik/dynamic/100-workspaces.yml` for workspace routes (`devnest-reg-{id}`).
- **WebSockets:** Traefik forwards Upgrade by default.
- **Static example routes** (`traefik/dynamic/000-base.yml`):
  - `ws-123.<DEVNEST_BASE_DOMAIN>` → `http://host.docker.internal:<port>` (host upstream).
  - `whoami.<DEVNEST_BASE_DOMAIN>` → `mock-upstream` container.

## Layout

```
devnest-gateway/
├── docker-compose.yml      # Traefik + route-admin + mock-upstream
├── .env.example
├── route_admin/            # FastAPI app (Dockerfile + route_admin_app.py)
├── traefik/
│   ├── traefik.yml         # Static: entrypoints, directory provider
│   └── dynamic/
│       ├── 000-base.yml    # Static examples (hand-edited)
│       └── 100-workspaces.yml   # Managed by route-admin
├── scripts/
│   ├── hosts-snippet.sh
│   └── run-example-upstream.sh
├── tests/
├── requirements-test.txt
├── pytest.ini
└── docs/
    └── BACKEND_INTEGRATION_CONTRACT.md
```

## Automated tests

From `devnest-gateway/`:

```bash
pip install -r requirements-test.txt
pytest tests -v                    # includes live Docker test (port 80 must be free)
pytest tests -v -m "not integration"   # YAML + compose file checks only
```

The **integration** test runs `docker compose up`, curls `whoami.app.devnest.local` via Traefik, then tears the stack down. Skip it locally if something already listens on host port 80.

## Quick start

1. **Copy env** (optional; compose has defaults):

   ```bash
   cd devnest-gateway
   cp .env.example .env
   ```

2. **Hosts file** (on macOS/Linux; Docker Desktop). Print suggested lines:

   ```bash
   ./scripts/hosts-snippet.sh
   ```

   Append the printed lines to `/etc/hosts` (or use your DNS tool). Default domain: `app.devnest.local`.

3. **Build and start** (first run builds `route-admin`):

   ```bash
   docker compose up -d --build
   ```

4. **Smoke test (no host server needed):**

   ```bash
   curl -sS -H "Host: whoami.app.devnest.local" http://127.0.0.1/
   ```

   You should see the `mock-upstream` nginx welcome HTML.

5. **End-to-end `ws-123` (upstream on host :8080):**

   In one terminal:

   ```bash
   ./scripts/run-example-upstream.sh
   ```

   In another:

   ```bash
   curl -sS -H "Host: ws-123.app.devnest.local" http://127.0.0.1/
   ```

   You should see the small HTML page from the example upstream.

6. **Dashboard** (dev only): http://127.0.0.1:8080/dashboard/ (Traefik v3 path).

7. **Route-admin** (optional): http://127.0.0.1:9080/health — backend sets `DEVNEST_GATEWAY_URL=http://127.0.0.1:9080` and `DEVNEST_GATEWAY_ENABLED=true` to register routes on workspace RUNNING.

## Configuration

| Item | Where |
|------|--------|
| Base domain for examples | `DEVNEST_BASE_DOMAIN` in `.env` (match `Host()` in `traefik/dynamic/*.yml`) |
| Gateway HTTP | Port **80** on host → Traefik `web` |
| Dashboard | Port **8080** on host → Traefik API |
| Route-admin | Port **9080** on host → `POST/DELETE /routes` |
| Static routes | `traefik/dynamic/000-base.yml` |
| Registered workspace routes | `traefik/dynamic/100-workspaces.yml` (written by route-admin) |

If you change the base domain, update `Host(\`...\`)` in the YAML files (or add templating later).

### Path-based routing (optional)

Add another router in `000-base.yml` with `PathPrefix(\`/api\`)` combined with `Host()` if needed.

## How this aligns with DevNest docs

- **Control plane** (backend): workspace lifecycle; worker calls route-admin when `DEVNEST_GATEWAY_ENABLED=true`.
- **Data plane** (this repo): Traefik + route-admin; maps **public host** → **upstream** and proxies traffic.

See `docs/BACKEND_INTEGRATION_CONTRACT.md` for the HTTP contract and metadata mapping.

## Deferred (next phases)

- Dynamic route registration from the backend / reconcile loops  
- Gateway admin API  
- TLS termination, public DNS (Route53), Kubernetes Ingress  
- Auth/session enforcement at the edge  
- Multi-instance gateway clusters and advanced load balancing  

## Troubleshooting

- **`host.docker.internal` on Linux:** Docker Compose may need `extra_hosts: host.docker.internal:host-gateway` (already present for the Traefik service on Linux-oriented setups; Docker Desktop sets this automatically).
- **curl without Host header:** Use `-H "Host: ws-123.app.devnest.local"` or add the hostname to `/etc/hosts` and `curl http://ws-123.app.devnest.local/`.
- **502 from Traefik:** Upstream not reachable from the Traefik container (wrong port, firewall, or nothing listening on the host).
