#!/usr/bin/env bash
# Idempotent EC2 deploy: sync repo to a remote branch and rebuild docker-compose.integration.yml stack.
# Intended to run ON the instance (via CI SSH). Optional env:
#   NEXT_PUBLIC_API_BASE_URL — e.g. http://<public-ip>:8000 for the browser UI build
#   DEVNEST_DEPLOY_DIR — repo path (default: ~/Devnest)
#   DEVNEST_DEPLOY_REPO_URL — git remote (default: upstream Devnest URL)

set -euo pipefail

BRANCH="${1:-main}"
REPO_DIR="${DEVNEST_DEPLOY_DIR:-${HOME}/Devnest}"
REPO_URL="${DEVNEST_DEPLOY_REPO_URL:-https://github.com/timothy-choi/Devnest.git}"
COMPOSE="${COMPOSE_FILE:-docker-compose.integration.yml}"

echo "Deploying DevNest from branch: ${BRANCH}"

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

# Workspace secure-context defaults for EC2/integration.
# code-server features like clipboard, webviews, and some extension flows work better over HTTPS.
# When not explicitly configured, prefer Traefik's TLS entrypoint for public workspace URLs.
if [[ -z "${DEVNEST_TLS_ENABLED:-}" ]]; then
  export DEVNEST_TLS_ENABLED="true"
  echo "DEVNEST_TLS_ENABLED unset: defaulting to true for secure workspace access."
fi

if [[ "${DEVNEST_TLS_ENABLED,,}" == "true" || "${DEVNEST_TLS_ENABLED,,}" == "1" || "${DEVNEST_TLS_ENABLED,,}" == "yes" || "${DEVNEST_TLS_ENABLED,,}" == "on" ]]; then
  if [[ -z "${DEVNEST_GATEWAY_PUBLIC_SCHEME:-}" ]]; then
    export DEVNEST_GATEWAY_PUBLIC_SCHEME="https"
    echo "DEVNEST_GATEWAY_PUBLIC_SCHEME unset: using https."
  fi
  if [[ -z "${DEVNEST_GATEWAY_PUBLIC_PORT:-}" ]]; then
    export DEVNEST_GATEWAY_PUBLIC_PORT="${DEVNEST_GATEWAY_TLS_PORT:-9443}"
    echo "DEVNEST_GATEWAY_PUBLIC_PORT unset: using ${DEVNEST_GATEWAY_PUBLIC_PORT}."
  fi
  if [[ -z "${DEVNEST_GATEWAY_TLS_PORT:-}" ]]; then
    export DEVNEST_GATEWAY_TLS_PORT="9443"
    echo "DEVNEST_GATEWAY_TLS_PORT unset: using ${DEVNEST_GATEWAY_TLS_PORT}."
  fi
fi

if [ "${BRANCH}" = "main" ]; then
  git checkout main
  git reset --hard origin/main
else
  git checkout "${BRANCH}" 2>/dev/null || git checkout -b "${BRANCH}" "origin/${BRANCH}"
  git reset --hard "origin/${BRANCH}"
fi

docker compose -f "${COMPOSE}" down || true
# Build workspace-image explicitly so devnest/workspace:latest always reflects Dockerfile.workspace
# on this host (Compose may otherwise reuse a stale :latest if cache is not invalidated).
docker compose -f "${COMPOSE}" build workspace-image
# --force-recreate ensures services pick up compose changes (e.g. pid: host for Linux topology attach).
docker compose -f "${COMPOSE}" up -d --build --force-recreate
docker compose -f "${COMPOSE}" ps

echo "--- workspace image (expected: Entrypoint = [\"/usr/bin/entrypoint.sh\"] only; Cmd without code-server) ---"
docker image inspect devnest/workspace:latest --format '{{json .Config.Labels}}' 2>/dev/null || true
docker image inspect devnest/workspace:latest --format 'Entrypoint={{json .Config.Entrypoint}} Cmd={{json .Config.Cmd}}' 2>/dev/null || true
if [[ -x "${REPO_DIR}/scripts/verify-workspace-image.sh" ]]; then
  "${REPO_DIR}/scripts/verify-workspace-image.sh" devnest/workspace:latest || true
fi

echo "--- gateway (browser IDE URL) ---"
echo "Compose enables DEVNEST_GATEWAY_ENABLED by default with Traefik on host port \${DEVNEST_GATEWAY_PORT:-9081} and TLS port \${DEVNEST_GATEWAY_TLS_PORT:-9443}."
echo "Attach returns gateway_url like \${DEVNEST_GATEWAY_PUBLIC_SCHEME:-http}://ws-<id>.<DEVNEST_BASE_DOMAIN>[:<DEVNEST_GATEWAY_PUBLIC_PORT>]/"
echo "On EC2 with Traefik on 443: export DEVNEST_GATEWAY_TLS_PORT=443 DEVNEST_GATEWAY_PUBLIC_PORT=0 before compose up."
echo "Browsers must resolve ws-<id>.<DEVNEST_BASE_DOMAIN> to this host's Traefik IP (sslip.io / real DNS / hosts)."
echo "--- deploy diagnostics ---"
git status || true
git rev-parse HEAD || true
docker compose -f "${COMPOSE}" ps || true
docker compose -f "${COMPOSE}" logs --no-color || true
