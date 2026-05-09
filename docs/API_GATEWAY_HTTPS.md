# API HTTPS via Traefik (EC2)

Expose the FastAPI control plane at **`https://api.devnest-app.com`** through the same Traefik container that serves workspace **legacy HTTP** on **`DEVNEST_GATEWAY_PORT`** (for example **`:9081`** → container **`web` :80**). Workspace/code-server traffic is **not** routed through Vercel and does not share this hostname.

## DNS

| Record | Type | Value | Notes |
|--------|------|--------|--------|
| `api.devnest-app.com` | **A** | **EC2 public IPv4** | Point directly at the instance (Cloudflare: **DNS only** / grey cloud while debugging TLS). |

Keep **`DEVNEST_BASE_DOMAIN`** (sslip / wildcard workspace DNS) unchanged; it is intentionally separate from `api.devnest-app.com`.

## What was added in-repo

| File | Purpose |
|------|--------|
| [`devnest-gateway/traefik/dynamic/050-api-public.yml`](../devnest-gateway/traefik/dynamic/050-api-public.yml) | `Host(\`api.devnest-app.com\`)` on entrypoint **`web`** → `http://backend:8000`. Works when Traefik’s **`web`** port is reachable (including **`9081:80`** with correct `Host` header). |
| [`devnest-gateway/traefik/dynamic/051-api-https-letsencrypt.yml.example`](../devnest-gateway/traefik/dynamic/051-api-https-letsencrypt.yml.example) | Copy to **`051-api-https-letsencrypt.yml`** to add **`websecure`** router + **`tls.certResolver: letsencrypt`**. |
| [`docker-compose.ec2-api-https.yml`](../docker-compose.ec2-api-https.yml) | Merge file: publish **`80:80`** and **`443:443`**, persist **`acme.json`** in volume **`devnest_traefik_acme`**. |
| [`scripts/validate-api-gateway.sh`](../scripts/validate-api-gateway.sh) | Quick **`curl`** checks for `/health`. |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| **`DEVNEST_ACME_EMAIL`** | Yes for Let’s Encrypt | Contact email in ACME account (set on Traefik service when using LE). |
| **`DEVNEST_GATEWAY_PORT`** | No | Workspace legacy HTTP (host → Traefik `web`). Default in compose **`9081`**. Leave as-is for sslip stacks. |

Traefik reads **`DEVNEST_ACME_EMAIL`** from its container environment when **`certificatesResolvers`** is enabled in [`devnest-gateway/traefik/traefik.yml`](../devnest-gateway/traefik/traefik.yml).

Do **not** set **`NEXT_PUBLIC_DEVNEST_WORKSPACE_DOMAIN_MODE=tenant`** for this change; tenant workspace routing is unrelated.

## Deployment steps (EC2 + Let’s Encrypt)

1. **DNS**: Create **`api.devnest-app.com` → A → EC2 public IP** (see table above).
2. **Security group**: Allow inbound **TCP 443** (and **TCP 80** if you use HTTP-01 challenge in `traefik.yml`).
3. **ACME static config**: In **`devnest-gateway/traefik/traefik.yml`**, uncomment the **`certificatesResolvers.letsencrypt`** block (prefer **`tlsChallenge: {}`** if only **443** is exposed publicly).
4. **Persist certs**: Merge **`docker-compose.ec2-api-https.yml`** so **`/etc/traefik/acme`** is a named volume (see file header).
5. **HTTPS router**:  
   `cp devnest-gateway/traefik/dynamic/051-api-https-letsencrypt.yml.example devnest-gateway/traefik/dynamic/051-api-https-letsencrypt.yml`
6. **Set email** in `.env.integration` (or shell):  
   `DEVNEST_ACME_EMAIL=you@yourdomain.com`
7. **Recreate Traefik** (example):  
   `docker compose --env-file .env.integration -f docker-compose.integration.yml -f docker-compose.ec2-api-https.yml up -d --force-recreate traefik`
8. **Backend / app**: Point **`NEXT_PUBLIC_API_BASE_URL`** (Vercel) and OAuth redirect bases at **`https://api.devnest-app.com`** when ready; enable **`AUTH_COOKIE_SECURE=true`** only after the UI is consistently HTTPS.

### Optional: HTTP → HTTPS for the API host only

After HTTPS works, you can add a **`redirectScheme`** middleware on the **`devnest-api-public-http`** router in **`050-api-public.yml`** (narrow **`rule`** to **`api.devnest-app.com`** only) so plain HTTP on **:80** redirects to **https://**. Keep workspace **`ws-*`** hosts on **:9081** without that middleware so legacy sslip HTTP is unaffected.

## Validation

From any machine that resolves **`api.devnest-app.com`** to your EC2:

```bash
./scripts/validate-api-gateway.sh https://api.devnest-app.com
```

Or manually:

```bash
curl -fsSI https://api.devnest-app.com/health
curl -fsS https://api.devnest-app.com/health
```

Expected body includes **`"status":"ok"`** (FastAPI liveness).

If HTTPS is not enabled yet, test the **`web`** route via the published gateway port (example **`9081`**):

```bash
curl -fsS -H 'Host: api.devnest-app.com' "http://<EC2_PUBLIC_IP>:9081/health"
```

## Staged rollout without Let’s Encrypt

- Keep **`051-api-https-letsencrypt.yml`** absent and ACME commented — only **`050-api-public.yml`** applies.
- Use **`9081:80`** (or **`80:80`** without TLS) for debugging; terminate TLS at Cloudflare (“Full strict”) later if preferred (origin cert or LE on Traefik).

## Related docs

- [`DEPLOYMENT.md`](DEPLOYMENT.md) — general gateway TLS notes  
- [`devnest-gateway/README.md`](../devnest-gateway/README.md) — Traefik + route-admin layout  
