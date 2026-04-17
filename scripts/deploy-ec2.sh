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
echo "Compose enables DEVNEST_GATEWAY_ENABLED by default with Traefik on host port \${DEVNEST_GATEWAY_PORT:-9081}."
echo "Attach returns gateway_url like http://ws-<id>.<DEVNEST_BASE_DOMAIN>[:<DEVNEST_GATEWAY_PUBLIC_PORT>]/"
echo "On EC2 with Traefik on 80: export DEVNEST_GATEWAY_PORT=80 DEVNEST_GATEWAY_PUBLIC_PORT=0 before compose up."
echo "DNS or /etc/hosts must resolve ws-<id>.<DEVNEST_BASE_DOMAIN> to this host (or your edge IP)."
echo "--- deploy diagnostics ---"
git status || true
git rev-parse HEAD || true
docker compose -f "${COMPOSE}" ps || true
docker compose -f "${COMPOSE}" logs --no-color || true
