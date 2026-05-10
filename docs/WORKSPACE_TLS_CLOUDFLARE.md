# Workspace HTTPS (wildcard) with Traefik + Cloudflare DNS-01

Workspace browsers use **`{DEVNEST_GATEWAY_PUBLIC_SCHEME}://ws-<id>-<hash>.<DEVNEST_BASE_DOMAIN>/`**. With **`DEVNEST_GATEWAY_PUBLIC_SCHEME=https`** and a normal (non-sslip) **`DEVNEST_BASE_DOMAIN`**, route-admin writes Traefik routers on **`websecure`** with **`tls.certResolver: letsencrypt-dns`** and a wildcard **`*.`** domain so Let’s Encrypt issues one certificate for all workspace hosts.

The control-plane API at **`https://api.<domain>`** keeps using the separate **`letsencrypt`** resolver (**TLS-ALPN-01**) in `devnest-gateway/traefik/traefik.yml`. Workspace traffic is **not** routed through Vercel.

## Required environment variables

| Variable | Where | Purpose |
|----------|--------|---------|
| **`CF_DNS_API_TOKEN`** | Traefik container (`docker-compose` → `traefik.environment`) | Cloudflare API token with **DNS:Edit** on `devnest-app.com` (zone). Used by Traefik’s **`letsencrypt-dns`** resolver. |
| **`DEVNEST_BASE_DOMAIN`** | Backend + route-admin + `.env.integration` | Apex host, e.g. **`devnest-app.com`**. Wildcard cert requests **`*.<DEVNEST_BASE_DOMAIN>`** and SAN **`DEVNEST_BASE_DOMAIN`**. |
| **`DEVNEST_GATEWAY_PUBLIC_SCHEME`** | Backend + `.env.integration` | Set **`https`** so `gateway_url` and route-admin emit **`websecure`** + TLS. |
| **`DEVNEST_GATEWAY_PUBLIC_PORT`** | Backend + `.env.integration` | Use **`0`** (or omit port in URLs) when using default **443**. |
| **`DEVNEST_ACME_EMAIL`** | Traefik (optional duplicate; static `traefik.yml` also sets ACME email) | Let’s Encrypt account contact. |

Optional legacy flag **`DEVNEST_TLS_ENABLED=true`** still forces **`websecure`**; without **`DEVNEST_BASE_DOMAIN`** (or on **sslip.io**), route-admin uses **`tls: {}`** (self-signed) instead of **`letsencrypt-dns`**.

## DNS (Cloudflare)

- **`*.devnest-app.com`** → origin (EC2) A/AAAA as you already use for workspaces.
- API subdomain **`api.devnest-app.com`** stays separate (TLS-ALPN or DNS per your API setup).
- The **Cloudflare token** must be allowed to create TXT records for **`_acme-challenge.*`**.

## Deploy / recycle

After changing env, recreate **Traefik** and **route-admin** so they pick up **`CF_DNS_API_TOKEN`** and **`DEVNEST_GATEWAY_PUBLIC_SCHEME`**, then let the backend re-register routes (or restart stack):

```bash
docker compose --env-file .env.integration \
  -f docker-compose.integration.yml \
  -f docker-compose.ec2-api-https.yml \
  up -d --force-recreate traefik route-admin
```

First **`https://`** request to a workspace host may pause while Traefik obtains the wildcard certificate.

## Validation

Replace `<workspace-host>` with a real workspace hostname (e.g. from attach **`gateway_url`** host part):

```bash
curl -v "https://<workspace-host>.devnest-app.com/"
```

Expect **HTTP/2 200** (or redirect from app) with a **valid** chain (not Traefik default cert). HTTP fallback:

```bash
curl -v "http://<workspace-host>.devnest-app.com/"
```

## Related

- [`API_GATEWAY_HTTPS.md`](API_GATEWAY_HTTPS.md) — API hostname + ports  
- [`devnest-gateway/traefik/traefik.yml`](../devnest-gateway/traefik/traefik.yml) — **`letsencrypt`** + **`letsencrypt-dns`**  
