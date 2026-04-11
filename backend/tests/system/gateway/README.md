# System tests: backend control plane + gateway data plane

## Prerequisites

- Docker Engine (daemon running)
- PostgreSQL reachable via `DATABASE_URL` (same as other `tests/system` control-plane tests)
- Repo root `docker-compose.system.yml` (Traefik + route-admin + `workspace-sim`)

## Run locally

From the **repository root**:

```bash
docker compose -f docker-compose.system.yml up -d --build
export DATABASE_URL='postgresql+psycopg://USER:PASS@localhost:5432/DBNAME'
export INTERNAL_API_KEY='dev-key'
cd backend
pytest tests/system/gateway -v
```

Stop the stack when finished:

```bash
docker compose -f docker-compose.system.yml down -v
```

## Ports

| Service      | Host port (default) |
|-------------|---------------------|
| route-admin | 19080               |
| Traefik web | 18080               |

Override with `ROUTE_ADMIN_SYSTEM_PORT` / `TRAEFIK_SYSTEM_PORT`.

## CI

GitHub Actions job **`system-gateway-tests`** runs these tests after starting the compose stack.

Merge-time **`system-tests`** excludes the `gateway` marker so this suite is not duplicated there.
