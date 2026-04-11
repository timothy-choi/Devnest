# DevNest Gateway (V1 — Traefik)

Standalone **data-plane** reverse proxy for workspace HTTP/WebSocket traffic. It is **not** the backend control plane (`/workspaces`, etc.). Control plane stays in `backend/`; this service only routes by hostname (and optional path) to upstreams.

## What works in V1

- Docker Compose runs **Traefik** (entrypoints **80** and **8080**) with **file provider** dynamic config.
- **Host-based routing** to upstream URLs (edit `traefik/dynamic.yml`).
- **WebSockets:** Traefik forwards Upgrade requests by default; no extra V1 config.
- **Example routes:**
  - `ws-123.<DEVNEST_BASE_DOMAIN>` → `http://host.docker.internal:<port>` (your IDE or mock server on the Docker **host**).
  - `whoami.<DEVNEST_BASE_DOMAIN>` → `mock-upstream` container (smoke test without anything on host :8080).

## Layout

```
devnest-gateway/
├── docker-compose.yml      # Traefik + optional mock-upstream
├── .env.example            # Copy to .env (same directory as compose file)
├── traefik/
│   ├── traefik.yml         # Static: entrypoints, providers, dashboard
│   └── dynamic.yml         # Routers/services (V1: edit by hand)
├── scripts/
│   ├── hosts-snippet.sh    # Print /etc/hosts lines for local testing
│   └── run-example-upstream.sh   # Python http.server on host :8080 for ws-123
├── tests/                  # Pytest: Traefik YAML + compose + optional live route
├── requirements-test.txt   # pytest + PyYAML (CI / dev)
├── pytest.ini
└── docs/
    └── BACKEND_INTEGRATION_CONTRACT.md   # Future backend → gateway sync
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

3. **Start Traefik:**

   ```bash
   docker compose up -d
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

## Configuration

| Item | Where |
|------|--------|
| Base domain for examples | `DEVNEST_BASE_DOMAIN` in `.env` (must match `Host()` rules in `dynamic.yml` if you change it) |
| Gateway HTTP | Port **80** on host → Traefik `web` |
| Dashboard | Port **8080** on host → Traefik `traefik` entrypoint |
| Routes / upstreams | `traefik/dynamic.yml` |

`docker-compose.yml` passes `DEVNEST_BASE_DOMAIN` into Traefik labels only for documentation; **router rules are in `dynamic.yml`**. If you change the base domain, update `Host(\`...\`)` in `dynamic.yml` or use a templating step later.

### Path-based routing (optional)

`dynamic.yml` includes a commented example: `PathPrefix(\`/api\`)` combined with `Host()`. Uncomment and adjust for APIs under a path on the same hostname.

## How this aligns with DevNest docs

- **Control plane** (backend): workspace CRUD, runtime metadata (`internal_endpoint`, `public_host`, `endpoint_ref`). Unchanged in this repo area.
- **Data plane** (this gateway): maps **public host** → **internal endpoint** and proxies traffic. V1 uses a static file; later a sync job will apply the same mapping from backend metadata.

See `docs/BACKEND_INTEGRATION_CONTRACT.md` for the field mapping table and future internal API shape.

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
