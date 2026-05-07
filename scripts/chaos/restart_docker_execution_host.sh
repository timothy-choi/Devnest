#!/usr/bin/env bash
# Restart Docker on an execution host that runs user workspace containers.
# All running workspace containers on that host will die; DevNest relies on DB state + reconcile/cleanup.
#
# Preconditions:
#   - SSH access with sudo for systemctl/docker
#   - Maintenance window or acceptance of workspace disruption on THAT host only
#
# Logs to watch:
#   - orchestrator.bringup.failed / workspace.job.failed
#   - workspace.job.retry_scheduled
#   - reconcile.started, workspace.recovery.reconcile
#   - autoscaler.scale_up.reason (if fleet tries to replace capacity)
#
# Usage:
#   SSH_TARGET=ec2-user@i-0123456789abcdef0 EXECUTION_HOST=10.0.1.50 ./scripts/chaos/restart_docker_execution_host.sh
#
set -euo pipefail

SSH_TARGET="${SSH_TARGET:?Set SSH_TARGET=user@host or user@ip}"
EXECUTION_HOST="${EXECUTION_HOST:-}"
DRY_RUN="${DRY_RUN:-0}"

usage() {
  cat <<'EOF'
Environment:
  SSH_TARGET       ssh destination (required), e.g. ec2-user@10.0.1.50
  EXECUTION_HOST   Optional echo-only label for runbook notes
  DRY_RUN          Set to 1 to print commands only

Restarts: sudo systemctl restart docker (systemd hosts).
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

echo "[chaos] Restarting Docker on ${SSH_TARGET} ${EXECUTION_HOST:+(execution ${EXECUTION_HOST})}"

if [[ "${DRY_RUN}" == "1" ]]; then
  echo 'ssh '"${SSH_TARGET}"' sudo systemctl restart docker || sudo service docker restart'
  exit 0
fi

ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "${SSH_TARGET}" \
  'sudo systemctl restart docker || sudo service docker restart'

echo "[chaos] Done. Verify placement nodes: scripts/devnest_ops_nodes.sh or DB execution_node + heartbeats."
