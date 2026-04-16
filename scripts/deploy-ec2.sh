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
docker compose -f "${COMPOSE}" up -d --build
docker compose -f "${COMPOSE}" ps

echo "--- deploy diagnostics ---"
git status || true
git rev-parse HEAD || true
docker compose -f "${COMPOSE}" ps || true
docker compose -f "${COMPOSE}" logs --no-color || true
