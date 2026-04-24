#!/usr/bin/env bash
# Idempotent EC2 deploy: sync repo to a remote branch and rebuild docker-compose.integration.yml stack.
# Intended to run ON the instance (via CI SSH). Optional env:
#   NEXT_PUBLIC_API_BASE_URL — e.g. http://<public-ip>:8000 for the browser UI build
#   DATABASE_URL — optional external Postgres/RDS DSN; when set, backend/worker use it (via DEVNEST_COMPOSE_DATABASE_URL)
#     instead of local compose Postgres
#   ${REPO_DIR}/.env.integration — optional; when present, sourced into this shell before validation and passed to
#     ``docker compose --env-file`` so RDS, S3, and OAuth vars survive non-interactive SSH reliably.
#   DEVNEST_DEPLOY_DIR — repo path (default: ~/Devnest)
#   DEVNEST_DEPLOY_REPO_URL — git remote (default: upstream Devnest URL)
#   DEVNEST_DEPLOY_GIT_REF — when set (e.g. CI ``github.sha``), check out this commit/tag after fetch instead of
#     resetting to ``origin/<branch>`` (first positional argument is then only used for logging).

set -euo pipefail

BRANCH="${1:-main}"
REPO_DIR="${DEVNEST_DEPLOY_DIR:-${HOME}/Devnest}"
REPO_URL="${DEVNEST_DEPLOY_REPO_URL:-https://github.com/timothy-choi/Devnest.git}"
COMPOSE="${COMPOSE_FILE:-docker-compose.integration.yml}"
INTEGRATION_ENV_FILE="${REPO_DIR}/.env.integration"

_devnest_load_integration_env_file() {
  if [[ -f "${INTEGRATION_ENV_FILE}" ]]; then
    echo "Loading ${INTEGRATION_ENV_FILE} into deploy shell (values not printed)."
    set -a
    # shellcheck disable=SC1091
    source "${INTEGRATION_ENV_FILE}"
    set +a
  fi
}

_devnest_compose() {
  if [[ -f "${INTEGRATION_ENV_FILE}" ]]; then
    docker compose --env-file "${INTEGRATION_ENV_FILE}" -f "${COMPOSE}" "$@"
  else
    docker compose -f "${COMPOSE}" "$@"
  fi
}

_devnest_deploy_env_presence() {
  echo "--- deploy env presence (no secret values) ---"
  if [[ -f "${INTEGRATION_ENV_FILE}" ]]; then
    echo ".env.integration: present (${INTEGRATION_ENV_FILE})"
  else
    echo ".env.integration: absent (compose uses shell env only)"
  fi
  if [[ -n "${DATABASE_URL:-}" ]]; then echo "DATABASE_URL: set"; else echo "DATABASE_URL: missing"; fi
  if [[ -n "${DEVNEST_COMPOSE_DATABASE_URL:-}" ]]; then echo "DEVNEST_COMPOSE_DATABASE_URL: set"; else echo "DEVNEST_COMPOSE_DATABASE_URL: missing"; fi
  echo "DEVNEST_SNAPSHOT_STORAGE_PROVIDER: ${DEVNEST_SNAPSHOT_STORAGE_PROVIDER:-<unset>}"
  if [[ -n "${DEVNEST_S3_SNAPSHOT_BUCKET:-}" ]]; then echo "DEVNEST_S3_SNAPSHOT_BUCKET: set"; else echo "DEVNEST_S3_SNAPSHOT_BUCKET: missing"; fi
  if [[ -n "${AWS_REGION:-}" ]]; then echo "AWS_REGION: set"; else echo "AWS_REGION: missing"; fi
  if [[ -n "${DEVNEST_S3_SNAPSHOT_PREFIX:-}" ]]; then echo "DEVNEST_S3_SNAPSHOT_PREFIX: set"; else echo "DEVNEST_S3_SNAPSHOT_PREFIX: missing"; fi
  if [[ -n "${DEVNEST_FRONTEND_PUBLIC_BASE_URL:-}" ]]; then echo "DEVNEST_FRONTEND_PUBLIC_BASE_URL: set"; else echo "DEVNEST_FRONTEND_PUBLIC_BASE_URL: missing"; fi
  if [[ -n "${GITHUB_OAUTH_PUBLIC_BASE_URL:-}" ]]; then echo "GITHUB_OAUTH_PUBLIC_BASE_URL: set"; else echo "GITHUB_OAUTH_PUBLIC_BASE_URL: missing"; fi
  if [[ -n "${GCLOUD_OAUTH_PUBLIC_BASE_URL:-}" ]]; then echo "GCLOUD_OAUTH_PUBLIC_BASE_URL: set"; else echo "GCLOUD_OAUTH_PUBLIC_BASE_URL: missing"; fi
  if [[ -n "${OAUTH_GITHUB_CLIENT_ID:-}" ]]; then echo "OAUTH_GITHUB_CLIENT_ID: set"; else echo "OAUTH_GITHUB_CLIENT_ID: missing"; fi
  if [[ -n "${OAUTH_GITHUB_CLIENT_SECRET:-}" ]]; then echo "OAUTH_GITHUB_CLIENT_SECRET: set"; else echo "OAUTH_GITHUB_CLIENT_SECRET: missing"; fi
  if [[ -n "${OAUTH_GOOGLE_CLIENT_ID:-}" ]]; then echo "OAUTH_GOOGLE_CLIENT_ID: set"; else echo "OAUTH_GOOGLE_CLIENT_ID: missing"; fi
  if [[ -n "${OAUTH_GOOGLE_CLIENT_SECRET:-}" ]]; then echo "OAUTH_GOOGLE_CLIENT_SECRET: set"; else echo "OAUTH_GOOGLE_CLIENT_SECRET: missing"; fi
  echo "---"
}

_devnest_load_integration_env_file

if [[ -n "${DEVNEST_DEPLOY_GIT_REF:-}" ]]; then
  echo "Deploying DevNest at ref: ${DEVNEST_DEPLOY_GIT_REF} (branch arg: ${BRANCH})"
else
  echo "Deploying DevNest from branch: ${BRANCH}"
fi
# Allow DEVNEST_DATABASE_URL to act as a friendlier alias for managed Postgres/RDS in CI/deploy
# environments, while Compose/runtime continue to consume DATABASE_URL consistently.
if [[ -z "${DATABASE_URL:-}" ]] && [[ -n "${DEVNEST_DATABASE_URL:-}" ]]; then
  export DATABASE_URL="${DEVNEST_DATABASE_URL}"
fi
# Compose maps ``DEVNEST_COMPOSE_DATABASE_URL`` into both ``DATABASE_URL`` and ``DEVNEST_DATABASE_URL`` in
# the backend container so the API and Alembic agree (see ``backend/app/libs/common/config.py``).
if [[ -n "${DATABASE_URL:-}" ]]; then
  export DEVNEST_COMPOSE_DATABASE_URL="${DATABASE_URL}"
  # Fail fast in backend/worker if compose still wired DATABASE_URL to bundled ``postgres`` by mistake.
  export DEVNEST_EXPECT_EXTERNAL_POSTGRES=true
  # Fail fast if DEVNEST_BASE_DOMAIN is still a client-loopback pattern (e.g. app.lvh.me) for remote browsers.
  export DEVNEST_EXPECT_REMOTE_GATEWAY_CLIENTS=true
  echo "External DATABASE_URL detected; control-plane services will target managed Postgres/RDS."
  # Same snapshot storage for API + workspace-worker (Settings enforces S3 when expect-* flags are set).
  _snap_provider="${DEVNEST_SNAPSHOT_STORAGE_PROVIDER:-}"
  if [[ "${_snap_provider}" != "s3" ]]; then
    echo "ERROR: External DATABASE_URL requires S3 snapshot storage (DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3)." >&2
    echo "Set DEVNEST_S3_SNAPSHOT_BUCKET and AWS_REGION (optional: DEVNEST_S3_SNAPSHOT_PREFIX, credentials)." >&2
    exit 1
  fi
  if [[ -z "${DEVNEST_S3_SNAPSHOT_BUCKET:-}" ]]; then
    echo "ERROR: DEVNEST_S3_SNAPSHOT_BUCKET must be set when using DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3." >&2
    exit 1
  fi
  if [[ -z "${AWS_REGION:-}" ]]; then
    echo "ERROR: AWS_REGION must be set when using DEVNEST_SNAPSHOT_STORAGE_PROVIDER=s3." >&2
    exit 1
  fi
  unset _snap_provider || true
elif [[ -n "${DEVNEST_REQUIRE_EXTERNAL_DB:-}" ]]; then
  echo "DEVNEST_REQUIRE_EXTERNAL_DB is set, but DATABASE_URL / DEVNEST_DATABASE_URL is empty." >&2
  exit 1
fi

mkdir -p "$(dirname "${REPO_DIR}")"
if [ ! -d "${REPO_DIR}/.git" ]; then
  git clone "${REPO_URL}" "${REPO_DIR}"
fi

cd "${REPO_DIR}"
git remote set-url origin "${REPO_URL}"
git fetch origin --prune

# Remote browsers must resolve ``ws-<id>.<DEVNEST_BASE_DOMAIN>`` to this instance (not 127.0.0.1 on the client).
# When unset on EC2, derive a globally resolvable base from the public IPv4 via sslip.io (hyphenated octets).
if [[ -z "${DEVNEST_BASE_DOMAIN:-}" ]]; then
  _meta_token=""
  if _meta_token="$(curl -sSf --connect-timeout 1 -X PUT \
    "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60" 2>/dev/null)"; then
    _pub_ip="$(curl -sSf --connect-timeout 1 -H "X-aws-ec2-metadata-token: ${_meta_token}" \
      "http://169.254.169.254/latest/meta-data/public-ipv4" 2>/dev/null)" || true
  else
    _pub_ip="$(curl -sSf --connect-timeout 1 "http://169.254.169.254/latest/meta-data/public-ipv4" 2>/dev/null)" || true
  fi
  if [[ -n "${_pub_ip:-}" ]] && [[ "${_pub_ip}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    export DEVNEST_BASE_DOMAIN="${_pub_ip//./-}.sslip.io"
    echo "DEVNEST_BASE_DOMAIN unset: using ${DEVNEST_BASE_DOMAIN} (EC2 public-ipv4 → sslip.io)."
  fi
  unset _meta_token _pub_ip || true
fi

# OAuth callbacks must return to the frontend app, not the API. Google rejects raw-IP redirect
# URIs for web OAuth clients, so prefer/normalize to an ``sslip.io`` hostname when possible.
if [[ -n "${DEVNEST_FRONTEND_PUBLIC_BASE_URL:-}" ]]; then
  _frontend_host="${DEVNEST_FRONTEND_PUBLIC_BASE_URL#http://}"
  _frontend_host="${_frontend_host#https://}"
  _frontend_host="${_frontend_host%%/*}"
  _frontend_name="${_frontend_host%%:*}"
  _frontend_port="${_frontend_host#${_frontend_name}}"
  if [[ "${_frontend_name}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    export DEVNEST_FRONTEND_PUBLIC_BASE_URL="http://${_frontend_name//./-}.sslip.io${_frontend_port:-:3000}"
    echo "DEVNEST_FRONTEND_PUBLIC_BASE_URL raw IPv4 normalized to ${DEVNEST_FRONTEND_PUBLIC_BASE_URL} for OAuth callbacks."
  fi
  unset _frontend_host _frontend_name _frontend_port || true
elif [[ -z "${DEVNEST_FRONTEND_PUBLIC_BASE_URL:-}" ]]; then
  _meta_token=""
  if _meta_token="$(curl -sSf --connect-timeout 1 -X PUT \
    "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60" 2>/dev/null)"; then
    _pub_ip="$(curl -sSf --connect-timeout 1 -H "X-aws-ec2-metadata-token: ${_meta_token}" \
      "http://169.254.169.254/latest/meta-data/public-ipv4" 2>/dev/null)" || true
  else
    _pub_ip="$(curl -sSf --connect-timeout 1 "http://169.254.169.254/latest/meta-data/public-ipv4" 2>/dev/null)" || true
  fi
  if [[ -n "${_pub_ip:-}" ]] && [[ "${_pub_ip}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    export DEVNEST_FRONTEND_PUBLIC_BASE_URL="http://${_pub_ip//./-}.sslip.io:3000"
    echo "DEVNEST_FRONTEND_PUBLIC_BASE_URL unset: using ${DEVNEST_FRONTEND_PUBLIC_BASE_URL} for OAuth callbacks."
  fi
  unset _meta_token _pub_ip || true
fi

# Browser bundle and client-side ``fetch`` use ``NEXT_PUBLIC_API_BASE_URL``. If unset, derive the
# public API origin from ``DEVNEST_FRONTEND_PUBLIC_BASE_URL`` (e.g. sslip :3000 → :8000) so remote
# users are not stuck calling ``localhost:8000`` from their laptops.
if [[ -z "${NEXT_PUBLIC_API_BASE_URL:-}" ]] && [[ -n "${DEVNEST_FRONTEND_PUBLIC_BASE_URL:-}" ]]; then
  _fe="${DEVNEST_FRONTEND_PUBLIC_BASE_URL}"
  if [[ "${_fe}" =~ :[0-9]+(/|$) ]]; then
    export NEXT_PUBLIC_API_BASE_URL="$(echo "${_fe}" | sed -E 's#:[0-9]+(/|$)#:8000\1#')"
  else
    export NEXT_PUBLIC_API_BASE_URL="${_fe}:8000"
  fi
  echo "NEXT_PUBLIC_API_BASE_URL unset: derived ${NEXT_PUBLIC_API_BASE_URL} from DEVNEST_FRONTEND_PUBLIC_BASE_URL."
  unset _fe || true
fi

if [[ -n "${DEVNEST_DEPLOY_GIT_REF:-}" ]]; then
  git fetch origin "${DEVNEST_DEPLOY_GIT_REF}" 2>/dev/null || true
  git fetch origin --prune
  git checkout -f "${DEVNEST_DEPLOY_GIT_REF}"
elif [ "${BRANCH}" = "main" ]; then
  git checkout main
  git reset --hard origin/main
else
  git checkout "${BRANCH}" 2>/dev/null || git checkout -b "${BRANCH}" "origin/${BRANCH}"
  git reset --hard "origin/${BRANCH}"
fi

_devnest_deploy_env_presence

_devnest_compose down || true
# Build workspace-image explicitly so devnest/workspace:latest always reflects Dockerfile.workspace
# on this host (Compose may otherwise reuse a stale :latest if cache is not invalidated).
_devnest_compose build workspace-image
# --force-recreate ensures services pick up compose changes (e.g. pid: host for Linux topology attach).
if [[ -n "${DATABASE_URL:-}" ]]; then
  echo "Skipping local postgres service because DATABASE_URL points to an external database."
  _devnest_compose up -d route-admin traefik
  _devnest_compose up -d --build --force-recreate --no-deps backend
  _devnest_compose up -d --build --force-recreate --no-deps workspace-worker
  _devnest_compose up -d --build --force-recreate --no-deps frontend
else
  _devnest_compose up -d --build --force-recreate
fi
_devnest_compose ps

echo "--- workspace image (expected: Entrypoint = [\"/usr/bin/entrypoint.sh\"] only; Cmd without code-server) ---"
docker image inspect devnest/workspace:latest --format '{{json .Config.Labels}}' 2>/dev/null || true
docker image inspect devnest/workspace:latest --format 'Entrypoint={{json .Config.Entrypoint}} Cmd={{json .Config.Cmd}}' 2>/dev/null || true
if [[ -x "${REPO_DIR}/scripts/verify-workspace-image.sh" ]]; then
  "${REPO_DIR}/scripts/verify-workspace-image.sh" devnest/workspace:latest || true
fi

echo "--- gateway (browser IDE URL) ---"
echo "Compose enables DEVNEST_GATEWAY_ENABLED by default with Traefik on host port \${DEVNEST_GATEWAY_PORT:-9081}."
echo "Attach returns gateway_url like http://ws-<id>.<DEVNEST_BASE_DOMAIN>[:<DEVNEST_GATEWAY_PUBLIC_PORT>]/"
echo "On EC2 with Traefik on 80: export DEVNEST_GATEWAY_PORT=80 DEVNEST_GATEWAY_PUBLIC_PORT=0 before compose up."
echo "Browsers must resolve ws-<id>.<DEVNEST_BASE_DOMAIN> to this host's Traefik IP (sslip.io / real DNS / hosts)."
echo "--- deploy diagnostics ---"
git status || true
git rev-parse HEAD || true
_devnest_compose ps || true
_devnest_compose logs --no-color || true
