# DevNest Security Model

## Overview

DevNest implements defence-in-depth with layered authentication controls covering user-facing APIs,
internal service communication, workspace access, and metrics exposition.

---

## JWT Secret Validation

### Problem

A default placeholder `JWT_SECRET_KEY` (`change-me-in-production`) shipped in the config is
insecure in any non-development environment. Without enforcement, accidental deployment with the
default key silently leaves all JWT tokens forgeable.

### Implementation

`app/libs/common/config.py` validates the JWT secret at startup via a Pydantic `@model_validator`:

1. **Always**: emits a `WARNING` log when `jwt_secret_key == "change-me-in-production"`.
2. **Abort** when either condition holds:
   - `DEVNEST_REQUIRE_SECRETS=true` — explicit opt-in regardless of environment.
   - `DEVNEST_ENV` is not `"development"` (i.e. `staging`, `production`, or any other value) —
     automatic enforcement that does not require operators to set `DEVNEST_REQUIRE_SECRETS`.

The check raises `RuntimeError` so the error surfaces clearly in logs and process supervisors
(systemd, Kubernetes, Docker Compose health checks).

### Configuration

| Variable | Default | Description |
|---|---|---|
| `JWT_SECRET_KEY` | `change-me-in-production` | JWT signing key |
| `DEVNEST_ENV` | `development` | Accepted: `development`, `staging`, `production` |
| `DEVNEST_REQUIRE_SECRETS` | `false` | Explicit enforcement flag |

**Generate a strong secret:**

```bash
openssl rand -hex 32
```

---

## Internal API Key Authentication

Internal endpoints (job processing, reconcile, notifications, metrics) are protected by scoped
`X-Internal-API-Key` header authentication.

### Scopes

| Scope | Settings field | Routes |
|---|---|---|
| `workspace_jobs` | `INTERNAL_API_KEY_WORKSPACE_JOBS` | `POST /internal/workspace-jobs/process` |
| `workspace_reconcile` | `INTERNAL_API_KEY_WORKSPACE_RECONCILE` | `POST /internal/workspace-reconcile/tick` |
| `autoscaler` | `INTERNAL_API_KEY_AUTOSCALER` | Autoscaler routes |
| `infrastructure` | `INTERNAL_API_KEY_INFRASTRUCTURE` | Infrastructure routes; also used for `/metrics` |
| `notifications` | `INTERNAL_API_KEY_NOTIFICATIONS` | Notification routes |

A legacy `INTERNAL_API_KEY` applies to all scopes when a scope-specific key is unset.

**Generate per-scope keys:**

```bash
for scope in workspace_jobs workspace_reconcile autoscaler infrastructure notifications; do
  echo "${scope}: $(openssl rand -hex 24)"
done
```

Set `DEVNEST_INTERNAL_API_KEY_MIN_LENGTH=24` to enforce minimum key length at startup.

---

## Metrics Endpoint Protection

`GET /metrics` exposes Prometheus telemetry including workspace queue depths, entity counts, and
internal auth failure counters.

### Problem

Without protection, any network client that can reach the API can read operational metrics,
potentially exposing workspace counts, job queue depths, and failure rates.

### Implementation

`app/libs/observability/routes.py` checks `DEVNEST_METRICS_AUTH_ENABLED`:

- **Disabled** (default `false`): endpoint is open; protect at the ingress/network layer.
- **Enabled** (`true`): requires `X-Internal-API-Key` validated against the `INFRASTRUCTURE` scope.
  Returns `HTTP 401` on missing or invalid key.

### Configuration

```bash
DEVNEST_METRICS_AUTH_ENABLED=true
INTERNAL_API_KEY_INFRASTRUCTURE=<strong-random-key>
```

**Prometheus scrape config with key:**

```yaml
scrape_configs:
  - job_name: devnest
    static_configs:
      - targets: ['api-host:8000']
    params: {}
    authorization: {}
    # Requires metrics auth header:
    authorization:
      credentials: <INTERNAL_API_KEY_INFRASTRUCTURE>
    # Or use custom_headers (Prometheus 2.49+):
    # params:
    #   X-Internal-API-Key: [<key>]
```

---

## Gateway ForwardAuth

Traefik calls `GET /internal/gateway/auth` before proxying every workspace request.

### Authentication Flow

```
Client request → Traefik
    ↓
ForwardAuth: GET /internal/gateway/auth
    Headers forwarded: X-Forwarded-Host, X-DevNest-Workspace-Session
    ↓
Backend validates:
  1. workspace_id extracted from X-Forwarded-Host (pattern: ws-{id}.{base_domain})
  2. Session token from X-DevNest-Workspace-Session looked up by HMAC-SHA256 hash
  3. Session must be ACTIVE and not expired
  4. Session must belong to the extracted workspace_id
  5. Workspace must be RUNNING
    ↓
200 → Traefik proxies to workspace upstream
401 → Traefik returns 401 Unauthorized to client
```

### Configuration

| Variable | Default | Description |
|---|---|---|
| `DEVNEST_GATEWAY_AUTH_ENABLED` | `false` | Enable session validation (dev bypass when false) |
| `DEVNEST_BASE_DOMAIN` | `app.devnest.local` | Expected base domain for workspace hostnames |

**Enable in production:**

```bash
DEVNEST_GATEWAY_AUTH_ENABLED=true
DEVNEST_BASE_DOMAIN=app.yourdomain.com
```

**Dev bypass**: When `DEVNEST_GATEWAY_AUTH_ENABLED=false` (the default), the endpoint returns
`200` unconditionally so local stacks work without session tokens.

---

## Container ID Handling

### Problem

Previous versions of lifecycle operations (`stop`, `delete`, `restart`, `update`, `check_health`)
derived a deterministic container name (`devnest-ws-{workspace_id}`) even when the engine-assigned
container ID was available in `WorkspaceRuntime.container_id`. If the persisted name and engine
reality diverged (e.g. after a host reboot or manual intervention), operations targeted the wrong
container.

### Fix

The `DefaultOrchestratorService` now accepts `container_id: str | None = None` on all lifecycle
methods. When provided, it is used directly as the `container_ref` for engine calls. When `None`,
the deterministic name is used as a backward-compatible fallback.

The worker (`workspace_job_worker/worker.py`) now:
1. Looks up `WorkspaceRuntime.container_id` from the database before calling `stop`, `delete`,
   `restart`, or `update`.
2. Passes the persisted value (if any) as `container_id` to the orchestrator.

The reconcile service applies the same pattern: `_reconcile_stopped` and `_reconcile_running`
both look up the persisted container ID before calling `check_workspace_runtime_health` and
`stop_workspace_runtime`.

---

## Workspace Session Tokens

- Tokens are opaque random strings with prefix `dnws_`.
- Only the HMAC-SHA256 hash (keyed with `jwt_secret_key`) is stored in the database.
- Sessions have a configurable TTL (`WORKSPACE_SESSION_TTL_SECONDS`, default 86400s).
- ForwardAuth validates token hash, expiry, workspace binding, and workspace status on every request.
- Session revocation is lazy (on next ForwardAuth check) or eager (on workspace stop/delete).

---

## Rate Limiting

In-process sliding-window rate limiter with no external dependencies:

| Layer | Scope | Default |
|---|---|---|
| Global (`RateLimitMiddleware`) | per-IP, all routes | 300 req/min |
| `auth_rate_limit` dependency | per-IP, auth endpoints | 20 req/min |
| `sse_rate_limit` dependency | per-IP, SSE endpoint | 30 req/min |

Disable globally with `DEVNEST_RATE_LIMIT_ENABLED=false` (dev/CI only).

---

## Password Security

- Passwords are hashed with bcrypt (Passlib).
- Password reset tokens are one-time-use, short-lived (default 60 min), and HMAC-keyed.
- `PASSWORD_RESET_RETURN_TOKEN=true` includes the reset token in the response (local/testing only;
  use email delivery in production).

---

## OAuth Provider Token Encryption

GitHub and Google OAuth access tokens stored for workspace operations are encrypted at rest with
Fernet (AES-256-CBC + HMAC-SHA256).

```bash
# Generate a Fernet key
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Set it
DEVNEST_TOKEN_ENCRYPTION_KEY=<output>
```

If unset, the JWT secret key is used as the derivation input (not recommended for production).

---

## Hardening Checklist

Before production deployment, verify:

- [ ] `JWT_SECRET_KEY` is a strong random value (e.g. `openssl rand -hex 32`)
- [ ] `DEVNEST_ENV=production` OR `DEVNEST_REQUIRE_SECRETS=true`
- [ ] Per-scope `INTERNAL_API_KEY_*` values set and different from each other
- [ ] `DEVNEST_INTERNAL_API_KEY_MIN_LENGTH=24` enforced
- [ ] `DEVNEST_GATEWAY_AUTH_ENABLED=true` (after TLS and session flows tested)
- [ ] `DEVNEST_METRICS_AUTH_ENABLED=true` OR `/metrics` restricted at ingress
- [ ] `DEVNEST_TOKEN_ENCRYPTION_KEY` set to a Fernet key
- [ ] `PASSWORD_RESET_RETURN_TOKEN=false` (default; use email delivery)
- [ ] `DATABASE_URL` uses a dedicated least-privilege database user
- [ ] `sslmode=require` appended to `DATABASE_URL` for encrypted DB connections
- [ ] S3 bucket has versioning enabled and IAM policy limits to the required actions
