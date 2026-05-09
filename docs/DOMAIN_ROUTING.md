# Multi-tenant domain routing

## Hybrid deployment (dashboard on Vercel, API + workspaces on EC2)

Typical production split:

| Traffic | Host example | Serves |
|--------|----------------|--------|
| Marketing / dashboard UI | `https://<apex>/` (e.g. dashboard origin from env) | Next.js on **Vercel** only |
| Control-plane REST API | `https://api.<zone>/` | FastAPI on **EC2** (or cloud LB → EC2) |
| Workspace IDE + WebSockets | `https://<route-subdomain>.<zone>/workspaces/<slug>` | Reverse proxy / Traefik on **EC2** → code-server (do **not** proxy IDE traffic through Vercel) |

**Frontend (Vercel)** — browser calls the API directly:

- `NEXT_PUBLIC_API_BASE_URL=https://api.<your-zone>`  
- `NEXT_PUBLIC_DEVNEST_PUBLIC_BASE_DOMAIN=<your-zone>` — enables tenant middleware (`*.zone` workspace hosts).
- `NEXT_PUBLIC_DEVNEST_APEX_URL=https://<apex-dashboard-origin>` — apex used for `/login?next=` redirects from tenant hosts (often same registrable zone without `www`, or your marketing host).
- `NEXT_PUBLIC_DEVNEST_WORKSPACE_DOMAIN_MODE=tenant` — open-workspace flows use **only** `public_url` / `workspace_url` from attach; never fall back to internal `gateway_url`.

**Backend (EC2)** — must agree with browser-visible origins:

- `DEVNEST_FRONTEND_PUBLIC_BASE_URL` — apex/dashboard origin shown to users (OAuth/UI diagnostics).
- `DEVNEST_API_PUBLIC_BASE_URL` — optional; logged at startup for operators (split-host EC2 API URL).
- Tenant workspace URLs: `DEVNEST_WORKSPACE_DOMAIN_MODE=tenant`, `DEVNEST_PUBLIC_BASE_DOMAIN`, `DEVNEST_PUBLIC_SCHEME`, `DEVNEST_PUBLIC_PORT` (often empty).
- Legacy/sslip stacks keep `DEVNEST_WORKSPACE_DOMAIN_MODE` unset or `legacy` and continue using `DEVNEST_BASE_DOMAIN` + `DEVNEST_GATEWAY_PUBLIC_*`.

**DNS (conceptual)** — no hostname literals are hardcoded in code; substitute your zone:

- Apex/dashboard → Vercel.
- `api.<zone>` → EC2 (FastAPI).
- `*.<zone>` wildcard → EC2 edge (Traefik/Caddy) for workspace hosts.

---

DevNest supports **tenant-style workspace URLs** when tenant routing is enabled (`DEVNEST_WORKSPACE_DOMAIN_MODE=tenant` and/or `DEVNEST_TENANT_SUBDOMAIN_ROUTING_ENABLED=true`; see table below):


```text
https://<route-subdomain>.<DEVNEST_PUBLIC_BASE_DOMAIN>/workspaces/<url-slug>
```

Example:

```text
https://tim.devnest.example.com/workspaces/eventrelay
```

Legacy routing (`ws-<workspace_id>.<DEVNEST_BASE_DOMAIN>/`) continues to work when tenant routing is disabled or during migration.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `DEVNEST_WORKSPACE_DOMAIN_MODE` | `tenant` \| `legacy` \| empty. **`tenant`** forces per-user workspace URLs; **`legacy`** forces `ws-<id>` URLs; empty follows `DEVNEST_TENANT_SUBDOMAIN_ROUTING_ENABLED`. |
| `DEVNEST_PUBLIC_BASE_DOMAIN` | Public apex host for tenant subdomains (wildcard DNS target). |
| `DEVNEST_PUBLIC_SCHEME` | `http` or `https` for generated workspace URLs. |
| `DEVNEST_PUBLIC_PORT` | Optional port on **browser** URLs (443/80 usually omitted). Tenant mode defaults to omitting `DEVNEST_GATEWAY_PUBLIC_PORT`. |
| `DEVNEST_TENANT_SUBDOMAIN_ROUTING_ENABLED` | Legacy flag; honored when `DEVNEST_WORKSPACE_DOMAIN_MODE` is empty. |
| `DEVNEST_BASE_DOMAIN` | Legacy gateway host suffix for `ws-<id>` routes and internal `gateway_url` in tenant mode. |
| `DEVNEST_FRONTEND_PUBLIC_BASE_URL` | Browser-visible dashboard/marketing origin (OAuth + diagnostics). |
| `DEVNEST_API_PUBLIC_BASE_URL` | Optional browser-visible API origin when API is split (e.g. `https://api…`). Logged at startup. |

## DNS

Create a **wildcard** record pointing at your edge load balancer or Traefik/Caddy:

- `*.devnest.example.com` → A/AAAA (or CNAME to LB hostname)

The left-most label is the per-user **route subdomain** (`UserAuth.route_subdomain_slug`), not the human-readable username unless they match by convention.

## TLS / HTTPS

- **Production:** terminate TLS at Traefik/Caddy with ACME (Let’s Encrypt DNS-01 recommended for wildcards) or your cloud LB certificate.
- **Local:** use `mkcert` or Traefik’s default certificate for `*.devnest.local`; map hosts in `/etc/hosts` or use `.localhost` where appropriate.

Generated URLs honor `DEVNEST_PUBLIC_SCHEME`, `DEVNEST_PUBLIC_PORT`, and (in **legacy** mode) `DEVNEST_GATEWAY_PUBLIC_PORT` when the gateway is not on 80/443.

## Traefik (route-admin)

Workspace routes are registered with:

- **Host:** ``<route_subdomain>.<public_base_domain>`` (wildcard router pattern at the edge), or legacy ``ws-<id>.<base_domain>``.
- **PathPrefix:** `/workspaces/<url_slug>` for tenant mode (strip prefix before upstream code-server).
- **ForwardAuth:** `GET /internal/gateway/auth` on the control plane must receive:
  - `X-Forwarded-Host` — original browser host.
  - `X-Forwarded-Uri` — original request URI (path + query), so the backend can parse `/workspaces/<slug>` **before** path stripping.

Ensure ForwardAuth runs **before** `stripPrefix` middleware so the auth service sees the full client path. Traefik’s `forwardAuth` typically forwards these headers when `trustForwardHeader: true`.

Enable WebSockets on the workspace service (Traefik handles upgrades by default; no special router flag needed beyond passing through `Connection` / `Upgrade` headers).

### Example static snippets

Base middleware (see `devnest-gateway/traefik/dynamic/000-base.yml`):

```yaml
http:
  middlewares:
    devnest-workspace-auth:
      forwardAuth:
        address: "http://backend:8000/internal/gateway/auth"
        trustForwardHeader: true
```

Tenant router shape (conceptual; route-admin emits concrete rules):

```yaml
# Pseudocode — actual hosts/path prefixes come from the route-admin API
http:
  routers:
    workspace-tim-eventrelay:
      rule: Host(`tim.devnest.example.com`) && PathPrefix(`/workspaces/eventrelay`)
      middlewares:
        - devnest-workspace-auth
        - strip-eventrelay
      service: workspace-upstream
  middlewares:
    strip-eventrelay:
      stripPrefix:
        prefixes:
          - /workspaces/eventrelay
```

## Caddy

Rough equivalent:

```caddyfile
*.devnest.example.com {
    encode zstd gzip
    @ws path /workspaces/*
    handle @ws {
        forward_auth http://backend:8000 {
            uri /internal/gateway/auth
            copy_headers X-Forwarded-Host X-Forwarded-Uri Cookie
        }
        reverse_proxy localhost:8080
    }
}
```

Adjust `reverse_proxy` targets to match your code-server sidecar. Preserve `X-Forwarded-Uri` (or `X-Forwarded-Request-Uri` depending on version) for ForwardAuth.

## Backend validation

`GET /internal/gateway/auth`:

1. Resolves **legacy** workspace id from `ws-<id>` hosts.
2. If tenant routing is enabled, parses **subdomain** with `parse_workspace_host` and **slug** with `extract_workspace_slug_from_path(X-Forwarded-Uri)`.
3. Loads `UserAuth` by `route_subdomain_slug`, then `Workspace` by `owner_user_id` + `url_slug`.
4. Validates the workspace **session** token against that resolved `workspace_id`.

Structured logs (JSON logger consumers):

- `routing.workspace_url_generated`
- `routing.legacy_url_generated`
- `routing.workspace_access_validated`
- `routing.subdomain_parsed`
- `routing.workspace_route_failed`
- `routing_mode_selected` (startup; `extra.routing_mode` = `tenant` \| `legacy`)
- `workspace.attach.public_url_selected`
- `workspace.attach.tenant_mode_enabled`

## Control-plane API

- `GET /workspaces/by-url-slug/{url_slug}` — authenticated lookup for dashboard / deep links.

Helpers (Python):

- `build_workspace_url(user, workspace, settings)`
- `parse_workspace_host(host, base_domain)`

## Authentication and apex vs tenant UI

- **Apex** (`https://<DEVNEST_PUBLIC_BASE_DOMAIN>`): marketing and account surfaces (`/`, `/login`, `/register`, `/signup`, `/pricing`, `/docs`, `/dashboard`).
- **Tenant hosts** (`https://<route_subdomain>.<base>/workspaces/...`): require auth cookies; signed-out visitors are redirected to `https://<apex>/login?next=<encoded-original-url>`.
- **Unknown subdomains**: middleware calls **`GET /auth/public/route-tenants/{subdomain}`**; **404** → apex **`/tenant-not-found`**. Hostnames never provision users.
- **Post-login `next`**: applied only when the URL tenant label matches **`route_subdomain_slug`** from **`GET /auth`**. Otherwise redirect **dashboard**. OAuth uses the same validation via HttpOnly **`devnest_oauth_next`**.
- **Shared cookies**: set **`AUTH_COOKIE_DOMAIN=.your-base-domain`** (or **`NEXT_PUBLIC_DEVNEST_COOKIE_DOMAIN`**) so session cookies reach tenant hosts.

| Frontend env | Purpose |
|----------------|---------|
| `NEXT_PUBLIC_DEVNEST_PUBLIC_BASE_DOMAIN` | Enables tenant middleware when set. |
| `NEXT_PUBLIC_DEVNEST_WORKSPACE_DOMAIN_MODE` | `tenant` \| `legacy` \| unset — controls whether the UI may fall back to attach `gateway_url`. |
| `NEXT_PUBLIC_DEVNEST_APEX_URL` | Optional apex origin for redirects. |
| `NEXT_PUBLIC_DEVNEST_PUBLIC_SCHEME` | Fallback scheme if apex URL omitted. |
| `NEXT_PUBLIC_API_BASE_URL` | Browser → FastAPI (e.g. `https://api…` on EC2 when UI is on Vercel). |
| `AUTH_COOKIE_DOMAIN` | Parent domain for HttpOnly auth cookies (e.g. `.example.com`). |

## Local development

1. Set `DEVNEST_PUBLIC_BASE_DOMAIN` to something resolvable locally (e.g. `devnest.local`).
2. Add `127.0.0.1 tim.devnest.local` (or use dnsmasq).
3. Enable tenant routing for path-based URLs: `DEVNEST_WORKSPACE_DOMAIN_MODE=tenant` (and/or `DEVNEST_TENANT_SUBDOMAIN_ROUTING_ENABLED=true`).
4. Run Traefik/Caddy + route-admin with the same base domain as the backend settings.
5. For quick iteration without TLS, set `DEVNEST_PUBLIC_SCHEME=http` and use plain HTTP entrypoints.

## Frontend

The Next.js app includes `/workspaces/[slug]` which resolves metadata via `GET /api/workspaces/by-url-slug/:slug`, then attaches with the numeric workspace id (cookies/session unchanged).

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| ForwardAuth always 401 “host not recognized” | Missing `X-Forwarded-Uri` in tenant mode; auth sees only `/internal/gateway/auth`. Fix Traefik/Caddy forwardAuth headers. |
| Wrong workspace opens | Path strip order: upstream receives stripped path but auth must use unstripped URI. |
| Cookie not sent to gateway | Cookie `Domain` must cover tenant hosts (e.g. `.devnest.example.com`). |
| Legacy URLs broken | Keep `DEVNEST_BASE_DOMAIN` aligned with existing `ws-*` routers until migration completes. |
| WebSocket 401 | Same ForwardAuth path — WS handshake must include session header/cookie and correct `X-Forwarded-Uri` under `/workspaces/<slug>/...`. |
