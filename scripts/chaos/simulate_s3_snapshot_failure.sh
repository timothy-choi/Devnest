#!/usr/bin/env bash
# Simulate snapshot upload/download failures against S3-backed snapshot storage.
#
# Preconditions:
#   - devnest_snapshot_storage_provider=s3 (or equivalent env from compose / EC2 deploy)
#   - Valid bucket configured in normal operation
#
# Techniques (manual — script does not mutate cloud resources unless DEVNEST_CHAOS_S3_BREAK=1):
#
#   A) Wrong bucket: export DEVNEST_S3_SNAPSHOT_BUCKET=devnest-snapshot-nonexistent-$RANDOM
#      Restart backend + workspace-worker; trigger CREATE_SNAPSHOT job.
#
#   B) Revoked credentials: rotate/delete temporary IAM keys mid-upload (staging only).
#
#   C) Bucket policy deny: explicit Deny s3:PutObject for the worker role (time-bounded).
#
# Logs / metrics:
#   - workspace.snapshot.failed (LogEvent.WORKSPACE_SNAPSHOT_FAILED)
#   - orchestrator.snapshot.export_started / export failures in orchestrator logs
#   - workspace.job.retry_scheduled if retryable
#
# Usage:
#   ./scripts/chaos/simulate_s3_snapshot_failure.sh           # print runbook
#   DEVNEST_CHAOS_S3_BREAK=1 ./scripts/chaos/simulate_s3_snapshot_failure.sh  # echo destructive hints only
#
set -euo pipefail

cat <<'EOF'
[chaos] S3 snapshot failure — runbook

Observability (structured messages):
  workspace.snapshot.failed
  orchestrator.snapshot.export_started
  workspace.job.retry_scheduled | workspace.job.failed_terminal

Recovery:
  - Restore correct DEVNEST_S3_* env / IAM policy / bucket lifecycle rules.
  - Re-queue snapshot or workspace reconcile after fixing storage.

Integration compose uses x-workspace-snapshot-env; edit .env.integration carefully.
EOF

if [[ "${DEVNEST_CHAOS_S3_BREAK:-}" == "1" ]]; then
  echo ""
  echo "[chaos] DEVNEST_CHAOS_S3_BREAK=1 — example break (DO NOT run in prod):"
  echo "  export DEVNEST_S3_SNAPSHOT_BUCKET=__nonexistent_bucket_chaos__"
  echo "  # restart worker + API containers, then enqueue snapshot job"
fi
