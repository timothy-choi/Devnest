# API HTTPS via Traefik (EC2)

Expose the FastAPI control plane at **`https://api.devnest-app.com`** through the same Traefik container that serves workspace **legacy HTTP** (for example **`http://ws-<id>.<DEVNEST_BASE_DOMAIN>:9081/`** when **`DEVNEST_GATEWAY_PUBLIC_PORT=9081`**). Workspace and code-server traffic stay on **`DEVNEST_BASE_DOMAIN`**; it is **not** routed through Vercel and does not use the API hostname.

## DNS (Cloudflare and others)

| Record | Type | Value | Notes |
|--------|------|--------|--------|
| `api.devnest-app.com` | **A** | **EC2 public IPv4** | Must resolve to the instance that runs Traefik. |

**Cloudflare**

- For **TLS-ALPN-01** (`tlsChallenge` on Traefik port 443), Let’s Encrypt must reach **your origin** on **TCP 443**. Set **`api.devnest-app.com`** to **DNS only** (grey cloud) while obtaining and renewing certificates.
- **Proxied** (orange cloud) terminates TLS at Cloudflare; the origin challenge on 443 will fail unless you switch to **DNS-01** in Traefik (not configured in the default static file).
- **`www.devnest-app.com`** (Vercel) is unrelated; do not point workspace **`DEVNEST_BASE_DOMAIN`** at Vercel for this gateway path.

Keep **`DEVNEST_BASE_DOMAIN`** (sslip / workspace DNS) unchanged; it is intentionally separate from `api.devnest-app.com`.

## Security groups (AWS)

| Direction | Protocol | Ports | Source / destination | Purpose |
|-----------|----------|-------|------------------------|---------|
| Inbound | TCP | **443** | `0.0.0.0/0` (or your clients) | Public HTTPS API + Let’s Encrypt **tlsChallenge** |
| Inbound | TCP | **80** | Same | Optional HTTP to Traefik `web` (API HTTP router, future HTTP-01) |
| Inbound | TCP | **9081** (and **9443** if used) | Same | **Legacy** host maps to Traefik `web` / `websecure` when the base compose ports are kept |
| Outbound | TCP | **8000** (or your API container port) | Traefik → `backend` on the Docker network | Forward to FastAPI (default compose service name `backend`) |

Traefik must be able to reach **`http://backend:8000`** on the compose network (no change from the existing HTTP router).

## What is in the repo

| File | Purpose |
|------|---------|
| [`devnest-gateway/traefik/dynamic/050-api-public.yml`](../devnest-gateway/traefik/dynamic/050-api-public.yml) | `Host(\`api.devnest-app.com\`)` on **`web`** → `http://backend:8000` (HTTP, ports **80** and **9081** when both are published). |
| [`devnest-gateway/traefik/dynamic/051-api-https-letsencrypt.yml`](../devnest-gateway/traefik/dynamic/051-api-https-letsencrypt.yml) | Same host on **`websecure`** with **`tls.certResolver: letsencrypt`**. |
| [`devnest-gateway/traefik/dynamic/051-api-https-letsencrypt.yml.example`](../devnest-gateway/traefik/dynamic/051-api-https-letsencrypt.yml.example) | Backup copy of the HTTPS router block. |
| [`devnest-gateway/traefik/traefik.yml`](../devnest-gateway/traefik/traefik.yml) | **`certificatesResolvers.letsencrypt`** with **`tlsChallenge: {}`**. |
| [`docker-compose.ec2-api-https.yml`](../docker-compose.ec2-api-https.yml) | Merge file: **`80:80`**, **`443:443`**, named volume **`devnest_traefik_acme`** → `/etc/traefik/acme`. |
| [`scripts/deploy-ec2.sh`](../scripts/deploy-ec2.sh) | Defaults **`COMPOSE_FILE`** to **`docker-compose.integration.yml:docker-compose.ec2-api-https.yml`**. |
| [`scripts/validate-api-gateway.sh`](../scripts/validate-api-gateway.sh) | **`curl`** checks for **`/health`**. |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| **`DEVNEST_ACME_EMAIL`** | Yes on EC2 for Let’s Encrypt | Contact email for the ACME account (Traefik container env). |
| **`COMPOSE_FILE`** | Optional | Default in **`deploy-ec2.sh`**: `docker-compose.integration.yml:docker-compose.ec2-api-https.yml`. Set to **`docker-compose.integration.yml`** alone to roll back the merge file. |
| **`DEVNEST_GATEWAY_PORT`** / **`DEVNEST_GATEWAY_PUBLIC_PORT`** | No | Legacy workspace URLs often keep **`:9081`** until you move clients to **port 80**. |

Do **not** set **`NEXT_PUBLIC_DEVNEST_WORKSPACE_DOMAIN_MODE=tenant`** for this gateway path; tenant workspace routing is out of scope here.

## Deployment (EC2, current model)

1. **DNS**: **`api.devnest-app.com` → A → EC2 public IP** (Cloudflare DNS only while certs are issued).
2. **Security group**: Inbound **TCP 443** (required); **TCP 80** recommended; **9081** if you rely on legacy published port.
3. **`.env.integration`**: set **`DEVNEST_ACME_EMAIL=you@yourdomain.com`** (and keep other required EC2 vars).
4. **Deploy** (on the instance, from the repo root — `deploy-ec2.sh` merges **`docker-compose.ec2-api-https.yml`** by default):

```bash
bash scripts/deploy-ec2.sh main
```

Or recreate Traefik only after changing gateway files:

```bash
docker compose --env-file .env.integration \
  -f docker-compose.integration.yml \
  -f docker-compose.ec2-api-https.yml \
  up -d --force-recreate traefik
```

5. **Backend / app**: When ready, set Vercel **`NEXT_PUBLIC_API_BASE_URL=https://api.devnest-app.com`**; enable **`AUTH_COOKIE_SECURE=true`** only after the browser consistently uses HTTPS.

### Optional: HTTP → HTTPS for the API host only

After HTTPS works, you can add a **`redirectScheme`** middleware on the **`devnest-api-public-http`** router in **`050-api-public.yml`** (narrow the **`rule`** to **`api.devnest-app.com`** only) so plain HTTP on **:80** redirects to **https://**. Keep workspace **`ws-*`** hosts without that middleware so legacy sslip HTTP on **:9081** stays unchanged.

## Validation

From any machine that resolves **`api.devnest-app.com`** to your EC2:

```bash
./scripts/validate-api-gateway.sh https://api.devnest-app.com
```

Manual checks:

```bash
curl -fsSI https://api.devnest-app.com/health
curl -fsS https://api.devnest-app.com/health
```

Expected body: **`{"status":"ok"`** (FastAPI liveness).

**HTTP (legacy port / Host header):**

```bash
curl -fsS -H 'Host: api.devnest-app.com' "http://<EC2_PUBLIC_IP>:9081/health"
curl -fsS -H 'Host: api.devnest-app.com' "http://<EC2_PUBLIC_IP>/health"
```

## Rollback

- Set **`COMPOSE_FILE=docker-compose.integration.yml`** (single file) and redeploy: removes **`:80`/`:443`** publish and the **ACME** volume mount from this merge (Traefik may lose persisted certs if the named volume is removed).
- To disable HTTPS for the API only, remove or rename **`051-api-https-letsencrypt.yml`** and comment out **`certificatesResolvers`** in **`traefik.yml`**, then recreate Traefik.

## Related docs

- [`DEPLOYMENT.md`](DEPLOYMENT.md) — general gateway TLS notes  
- [`devnest-gateway/README.md`](../devnest-gateway/README.md) — Traefik + route-admin layout  
