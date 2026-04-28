#!/usr/bin/env bash
# One-command integration stack: validate .env.integration, docker compose up, health checks.
# Usage (repo root): ./scripts/deploy_integration.sh
# Override env file: ENV_FILE=.env.custom ./scripts/deploy_integration.sh
#
# Does not print secrets. Exits non-zero on validation or health failure.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE="${ENV_FILE:-.env.integration}"
COMPOSE_FILE="docker-compose.integration.yml"

info() { echo "[deploy-integration] $*"; }
warn() { echo "[deploy-integration] WARN: $*" >&2; }
die() { echo "[deploy-integration] ERROR: $*" >&2; exit 1; }

[[ -f "$ENV_FILE" ]] || die "Missing env file: ${ENV_FILE} (copy from .env.integration.example)"

# Load variables for validation (do not echo values).
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# Local/CI smoke may use an empty env file; production EC2 deploy is validated by scripts/setup-env.sh.
export DEVNEST_WORKSPACE_CONTAINER_IMAGE="${DEVNEST_WORKSPACE_CONTAINER_IMAGE:-devnest/workspace:latest}"

EFFECTIVE_DB="${DEVNEST_COMPOSE_DATABASE_URL:-}"
if [[ -z "${EFFECTIVE_DB}" ]]; then
  EFFECTIVE_DB="${DATABASE_URL:-}"
fi

EXPECT_EXT="${DEVNEST_EXPECT_EXTERNAL_POSTGRES:-false}"
EXPECT_REMOTE="${DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS:-false}"

validate_database_url() {
  local url="$1"
  if [[ -z "$url" ]]; then
    info "No DATABASE_URL / DEVNEST_COMPOSE_DATABASE_URL in ${ENV_FILE}; compose will use bundled Postgres default."
    return 0
  fi
  local trimmed="${url#"${url%%[![:space:]]*}"}"
  trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
  if [[ "$trimmed" =~ ^host= ]]; then
    die "Database config looks like a libpq keyword DSN (starts with host=). Use a SQLAlchemy URL, e.g. postgresql+psycopg://USER:PASS@HOST:5432/DBNAME?sslmode=require"
  fi
  if [[ "$trimmed" != *"://"* ]]; then
    die "Database URL must include a scheme (e.g. postgresql+psycopg://...), not a bare host= / keyword line."
  fi
  case "$trimmed" in
    postgresql://*|postgresql+psycopg://*|postgres://*) ;;
    *) die "Database URL must start with postgresql://, postgresql+psycopg://, or postgres:// (got scheme other than postgres)." ;;
  esac
  info "Non-empty database URL validated (credentials not printed)."
}

needs_s3_snapshot_storage() {
  if [[ "$EXPECT_EXT" == "true" ]] || [[ "$EXPECT_REMOTE" == "true" ]]; then
    return 0
  fi
  if [[ -n "$EFFECTIVE_DB" ]] && [[ "$EFFECTIVE_DB" != *"@postgres:"* ]]; then
    return 0
  fi
  return 1
}

validate_s3_when_required() {
  if ! needs_s3_snapshot_storage; then
    info "S3 snapshot storage not required for this posture (bundled Postgres + no DEVNEST_EXPECT_* flags)."
    return 0
  fi
  local prov="${DEVNEST_SNAPSHOT_STORAGE_PROVIDER:-local}"
  if [[ "${prov}" != "s3" ]]; then
    die "DEVNEST_SNAPSHOT_STORAGE_PROVIDER must be s3 when using external Postgres or DEVNEST_EXPECT_* is true (got: ${prov})."
  fi
  [[ -n "${DEVNEST_S3_SNAPSHOT_BUCKET:-}" ]] || die "DEVNEST_S3_SNAPSHOT_BUCKET is required when DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3"
  [[ -n "${AWS_REGION:-}" ]] || die "AWS_REGION is required when DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3"
  info "S3 snapshot storage: provider=s3 bucket=<set> region=${AWS_REGION}"
}

validate_oauth_pairs() {
  local gh_id gh_sec gl_id gl_sec
  gh_id="${OAUTH_GITHUB_CLIENT_ID:-${GITHUB_CLIENT_ID:-}}"
  gh_sec="${OAUTH_GITHUB_CLIENT_SECRET:-${GITHUB_CLIENT_SECRET:-}}"
  if [[ -n "$gh_id" && -z "$gh_sec" ]]; then
    die "GitHub OAuth: client id is set but client secret is missing (check OAUTH_GITHUB_* or GITHUB_*)."
  fi
  if [[ -z "$gh_id" && -n "$gh_sec" ]]; then
    die "GitHub OAuth: client secret is set but client id is missing."
  fi
  gl_id="${OAUTH_GOOGLE_CLIENT_ID:-${GOOGLE_CLIENT_ID:-}}"
  gl_sec="${OAUTH_GOOGLE_CLIENT_SECRET:-${GOOGLE_CLIENT_SECRET:-}}"
  if [[ -n "$gl_id" && -z "$gl_sec" ]]; then
    die "Google OAuth: client id is set but client secret is missing (check OAUTH_GOOGLE_* or GOOGLE_*)."
  fi
  if [[ -z "$gl_id" && -n "$gl_sec" ]]; then
    die "Google OAuth: client secret is set but client id is missing."
  fi
  if [[ -n "$gh_id" ]]; then
    info "GitHub OAuth: client id+secret present."
  fi
  if [[ -n "$gl_id" ]]; then
    info "Google OAuth: client id+secret present."
  fi
  if [[ -z "$gh_id" && -z "$gl_id" ]]; then
    warn "No OAuth client ids configured; only email/password auth will be available."
  fi
}

validate_public_urls() {
  local fe="${DEVNEST_FRONTEND_PUBLIC_BASE_URL:-}"
  if [[ -z "$fe" ]]; then
    warn "DEVNEST_FRONTEND_PUBLIC_BASE_URL is unset; compose defaults to http://localhost:3000 (OK for same-host dev)."
  else
    case "$fe" in
      http://*|https://*) info "DEVNEST_FRONTEND_PUBLIC_BASE_URL is set (scheme OK)." ;;
      *) die "DEVNEST_FRONTEND_PUBLIC_BASE_URL must be an http(s) URL." ;;
    esac
  fi
  local bd="${DEVNEST_BASE_DOMAIN:-}"
  if [[ "$EXPECT_REMOTE" == "true" ]]; then
    [[ -n "$bd" ]] || die "DEVNEST_BASE_DOMAIN is required when DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS=true"
    if [[ "${bd}" == "app.lvh.me" ]] || [[ "${bd}" == "app.devnest.local" ]]; then
      die "DEVNEST_BASE_DOMAIN=${bd} is not valid for remote clients when DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS=true."
    fi
    info "DEVNEST_BASE_DOMAIN=${bd} (remote posture)"
    case "${fe}" in
      *localhost*|*127.0.0.1*)
        die "DEVNEST_FRONTEND_PUBLIC_BASE_URL must not use localhost when DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS=true."
        ;;
    esac
  fi
}

dc() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

info "=== Pre-start validation (${ENV_FILE}) ==="
validate_database_url "${EFFECTIVE_DB}"
validate_s3_when_required
validate_oauth_pairs
validate_public_urls
info "Pre-start validation OK."

info "=== Starting stack (build + up -d) ==="
dc up -d --build

wait_backend_healthy() {
  local cid="" health="" i
  for ((i = 0; i < 90; i++)); do
    cid="$(dc ps -q backend 2>/dev/null || true)"
    if [[ -n "$cid" ]]; then
      health="$(docker inspect "$cid" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-health{{end}}' 2>/dev/null || echo none)"
      if [[ "$health" == "healthy" ]]; then
        return 0
      fi
    fi
    sleep 2
  done
  return 1
}

info "=== Waiting for backend health (GET /ready in container) ==="
if ! wait_backend_healthy; then
  die "backend did not become healthy in time. Try: docker compose --env-file ${ENV_FILE} -f ${COMPOSE_FILE} logs backend --tail 200"
fi
info "backend container reports healthy."

info "=== Service status ==="
dc ps

info "=== Post-start checks ==="

cid_backend="$(dc ps -q backend)"
cid_worker="$(dc ps -q workspace-worker)"
cid_frontend="$(dc ps -q frontend)"

[[ -n "$cid_backend" ]] || die "backend container not found"
[[ -n "$cid_worker" ]] || die "workspace-worker container not found"
[[ -n "$cid_frontend" ]] || die "frontend container not found"

if [[ "$(docker inspect "$cid_worker" --format '{{.State.Running}}')" != "true" ]]; then
  die "workspace-worker is not running"
fi
if [[ "$(docker inspect "$cid_frontend" --format '{{.State.Running}}')" != "true" ]]; then
  die "frontend is not running"
fi
info "workspace-worker and frontend containers are running."

info "Host probe: GET http://127.0.0.1:8000/ready"
if ! curl -sfS "http://127.0.0.1:8000/ready" >/dev/null; then
  die "curl http://127.0.0.1:8000/ready failed (is port 8000 published?)"
fi
info "Backend /ready OK from host."

info "frontend → backend: resolve backend + GET /ready via Node fetch"
if ! dc exec -T frontend node -e "fetch('http://backend:8000/ready').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))" >/dev/null 2>&1; then
  die "frontend container could not reach http://backend:8000/ready"
fi
info "frontend → backend connectivity OK."

info "Host probe: GET http://127.0.0.1:3000/ (frontend listening)"
wait_frontend_root() {
  local i
  for ((i = 0; i < 30; i++)); do
    if curl -sfS "http://127.0.0.1:3000/" >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  return 1
}
if ! wait_frontend_root; then
  die "Frontend root did not respond on http://127.0.0.1:3000/ in time."
fi
info "Frontend root OK from host."

info "workspace-worker snapshot_storage startup line (last match in recent logs):"
snap_line="$(dc logs --no-color --tail 400 workspace-worker 2>&1 | grep -F 'workspace-worker startup snapshot_storage provider=' | tail -1)" || true
if [[ -z "${snap_line}" ]]; then
  warn "Could not find snapshot_storage startup line in recent worker logs (worker may still be starting)."
else
  echo "${snap_line}"
fi
if needs_s3_snapshot_storage; then
  if [[ -n "${snap_line}" ]] && ! echo "${snap_line}" | grep -q 'provider=s3'; then
    die "Expected snapshot_storage provider=s3 in worker logs for this posture."
  fi
  info "Worker snapshot provider check: s3 (required posture) OK."
fi

info "execution node heartbeat (recent workspace-worker logs, non-fatal):"
hb_line="$(dc logs --no-color --tail 400 workspace-worker 2>&1 | grep -E 'execution_node_heartbeat_(emitter_started|success|emitted|emitted_via_http)' | tail -1)" || true
if [[ -z "${hb_line}" ]]; then
  warn "No execution_node_heartbeat_* line yet (check INTERNAL_API_BASE_URL, DEVNEST_NODE_HEARTBEAT_ENABLED, INTERNAL_API_KEY). See docs/EXECUTION_NODE_HEARTBEAT.md."
else
  echo "${hb_line}"
  info "Worker execution-node heartbeat log line found (freshness is scheduling-only when DEVNEST_REQUIRE_FRESH_NODE_HEARTBEAT=true)."
fi

info "=== DevNest integration stack is up ==="
info "UI: http://127.0.0.1:3000  API: http://127.0.0.1:8000  (adjust if you mapped different host ports)"
