#!/usr/bin/env bash
# Abruptly terminate an EC2 instance backing an execution node (pull plug simulation).
#
# Expected platform behavior (high level):
#   - Heartbeats stop; node may move NOT_READY / ERROR / TERMINATED depending on janitor + autoscaler paths
#   - RUNNING workspaces on that node become inconsistent with reality → reconcile jobs / operator action
#   - Autoscaler may provision replacement EC2 when configured (grep autoscaler.scale_up.reason)
#
# Logs:
#   - placement / heartbeat failures (node execution heartbeat handlers)
#   - autoscaler.capacity.*, autoscaler.scale_up.reason
#   - ec2_orphan_janitor_* when DEVNEST_EC2_ORPHAN_JANITOR_ENABLED
#
# Usage:
#   INSTANCE_ID=i-0123456789abcdef0 AWS_REGION=us-east-1 ./scripts/chaos/terminate_ec2_execution_node.sh
#   CONFIRM=yes INSTANCE_ID=i-0abc AWS_REGION=us-east-1 ./scripts/chaos/terminate_ec2_execution_node.sh
#
set -euo pipefail

INSTANCE_ID="${INSTANCE_ID:?Set INSTANCE_ID=i-...}"
AWS_REGION="${AWS_REGION:?Set AWS_REGION=...}"
CONFIRM="${CONFIRM:-}"

if [[ "${CONFIRM}" != "yes" ]]; then
  echo "[chaos] Refusing to terminate ${INSTANCE_ID} without CONFIRM=yes (DESTRUCTIVE)." >&2
  exit 2
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "[chaos] aws CLI not found" >&2
  exit 1
fi

echo "[chaos] Terminating ${INSTANCE_ID} in ${AWS_REGION}"
aws ec2 terminate-instances --region "${AWS_REGION}" --instance-ids "${INSTANCE_ID}"

echo "[chaos] Request submitted. Poll: aws ec2 describe-instances --instance-ids ${INSTANCE_ID} ..."
echo "[chaos] Then verify DevNest execution_node rows + workspace statuses + autoscaler logs."
