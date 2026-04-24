#!/usr/bin/env bash
# Write ~/Devnest/.env.integration (or ${DEVNEST_DEPLOY_DIR}/.env.integration) for EC2 / CI deploy.
# Caller sets DEVNEST_CI_WRITE_* env vars (typically GitHub Actions interpolates secrets into the SSH script).
# Never prints secret values. File mode 0600.

set -euo pipefail

REPO="${DEVNEST_DEPLOY_DIR:-${HOME}/Devnest}"
OUT="${REPO}/.env.integration"

_db="${DEVNEST_CI_WRITE_DATABASE_URL:-}"
_bucket="${DEVNEST_CI_WRITE_S3_BUCKET:-}"
_region="${DEVNEST_CI_WRITE_AWS_REGION:-}"
_prefix="${DEVNEST_CI_WRITE_S3_PREFIX:-devnest-snapshots}"
_provider="${DEVNEST_CI_WRITE_SNAPSHOT_PROVIDER:-s3}"
_ak="${DEVNEST_CI_WRITE_AWS_ACCESS_KEY_ID:-}"
_sk="${DEVNEST_CI_WRITE_AWS_SECRET_ACCESS_KEY:-}"

umask 077
mkdir -p "${REPO}"
: >"${OUT}"
chmod 600 "${OUT}"

printf '%s\n' "DATABASE_URL=${_db}" >>"${OUT}"
printf '%s\n' "DEVNEST_COMPOSE_DATABASE_URL=${_db}" >>"${OUT}"
printf '%s\n' "DEVNEST_DATABASE_URL=${_db}" >>"${OUT}"
printf '%s\n' "DEVNEST_SNAPSHOT_STORAGE_PROVIDER=${_provider}" >>"${OUT}"
printf '%s\n' "DEVNEST_S3_SNAPSHOT_BUCKET=${_bucket}" >>"${OUT}"
printf '%s\n' "DEVNEST_S3_SNAPSHOT_PREFIX=${_prefix}" >>"${OUT}"
printf '%s\n' "AWS_REGION=${_region}" >>"${OUT}"
if [[ -n "${_ak}" ]]; then
  printf '%s\n' "AWS_ACCESS_KEY_ID=${_ak}" >>"${OUT}"
fi
if [[ -n "${_sk}" ]]; then
  printf '%s\n' "AWS_SECRET_ACCESS_KEY=${_sk}" >>"${OUT}"
fi

echo "Wrote ${OUT} (mode 600). Presence: DATABASE_URL=$([[ -n "${_db}" ]] && echo ok || echo missing) DEVNEST_S3_SNAPSHOT_BUCKET=$([[ -n "${_bucket}" ]] && echo ok || echo missing) AWS_REGION=$([[ -n "${_region}" ]] && echo ok || echo missing) DEVNEST_SNAPSHOT_STORAGE_PROVIDER=${_provider}"

unset _db _bucket _region _prefix _provider _ak _sk || true
