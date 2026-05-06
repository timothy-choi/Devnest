#!/usr/bin/env bash
# Kill the workspace job worker mid-flight (simulates host crash, OOM killer, or deploy rollout).
#
# Typical stack: docker-compose.integration.yml service `workspace-worker`.
#
# Recovery signals (grep logs / structured logging):
#   - workspace.job.retry_scheduled  (also workspace_job_retry_scheduled in extra logs)
#   - workspace.retry.attempt
#   - reconcile.started / workspace.recovery.reconcile (when reconcile loop / manual reconcile fires)
#
# Usage:
#   ENV_FILE=/path/to.env ./scripts/chaos/kill_workspace_worker.sh
#   COMPOSE_FILE=docker-compose.integration.yml WORKER_SERVICE=workspace-worker ./scripts/chaos/kill_workspace_worker.sh --restart
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.integration.yml}"
ENV_FILE="${ENV_FILE:-}"
WORKER_SERVICE="${WORKER_SERVICE:-workspace-worker}"
SIGNAL="${SIGNAL:-SIGKILL}"

usage() {
  cat <<'EOF'
Usage: kill_workspace_worker.sh [--restart]

Environment:
  COMPOSE_FILE     Compose file (default: docker-compose.integration.yml)
  ENV_FILE         Optional --env-file path for docker compose
  WORKER_SERVICE   Service name (default: workspace-worker)
  SIGNAL           Signal to send (default: SIGKILL). Use SIGTERM for graceful trials.

After chaos:
  docker compose ... up -d workspace-worker   # if not using --restart
  curl -X POST "$API/internal/workspace-jobs/process?limit=5" -H "X-Internal-API-Key: ..."
EOF
}

DO_RESTART=0
for arg in "$@"; do
  case "${arg}" in
    -h|--help) usage; exit 0 ;;
    --restart) DO_RESTART=1 ;;
    *) echo "Unknown arg: ${arg}" >&2; usage; exit 1 ;;
  esac
done

compose() {
  if [[ -n "${ENV_FILE}" ]]; then
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
  else
    docker compose -f "${COMPOSE_FILE}" "$@"
  fi
}

echo "[chaos] Sending ${SIGNAL} to ${WORKER_SERVICE} (${COMPOSE_FILE})"
if [[ "${DO_RESTART}" -eq 1 ]]; then
  compose "kill" "-s" "${SIGNAL}" "${WORKER_SERVICE}" || true
  sleep 2
  compose "up" "-d" "${WORKER_SERVICE}"
  echo "[chaos] Worker restarted. Tail logs: compose logs -f ${WORKER_SERVICE}"
else
  compose "kill" "-s" "${SIGNAL}" "${WORKER_SERVICE}" || true
  echo "[chaos] Worker killed. Restart manually, then POST /internal/workspace-jobs/process"
fi
