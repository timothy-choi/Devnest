#!/usr/bin/env bash
# Write ~/Devnest/.env.integration (or ${DEVNEST_DEPLOY_DIR}/.env.integration) for EC2 / CI deploy.
# Caller sets DEVNEST_CI_WRITE_* env vars (typically GitHub Actions interpolates secrets into the SSH script).
# Never prints OAuth client secrets or database passwords. File mode 0600.

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

_fe="${DEVNEST_CI_WRITE_FRONTEND_PUBLIC_BASE_URL:-}"
_gh_pub="${DEVNEST_CI_WRITE_GITHUB_OAUTH_PUBLIC_BASE_URL:-${_fe}}"
_gc_pub="${DEVNEST_CI_WRITE_GCLOUD_OAUTH_PUBLIC_BASE_URL:-${_fe}}"

_ogh_id="${DEVNEST_CI_WRITE_OAUTH_GITHUB_ID:-}"
_ogh_sec="${DEVNEST_CI_WRITE_OAUTH_GITHUB_SECRET:-}"
_ogo_id="${DEVNEST_CI_WRITE_OAUTH_GOOGLE_ID:-}"
_ogo_sec="${DEVNEST_CI_WRITE_OAUTH_GOOGLE_SECRET:-}"

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

# OAuth: backend Settings read OAUTH_* (and aliases); public bases must be the browser-visible UI origin.
printf '%s\n' "DEVNEST_FRONTEND_PUBLIC_BASE_URL=${_fe}" >>"${OUT}"
printf '%s\n' "GITHUB_OAUTH_PUBLIC_BASE_URL=${_gh_pub}" >>"${OUT}"
printf '%s\n' "GCLOUD_OAUTH_PUBLIC_BASE_URL=${_gc_pub}" >>"${OUT}"
printf '%s\n' "OAUTH_GITHUB_CLIENT_ID=${_ogh_id}" >>"${OUT}"
printf '%s\n' "OAUTH_GITHUB_CLIENT_SECRET=${_ogh_sec}" >>"${OUT}"
printf '%s\n' "OAUTH_GOOGLE_CLIENT_ID=${_ogo_id}" >>"${OUT}"
printf '%s\n' "OAUTH_GOOGLE_CLIENT_SECRET=${_ogo_sec}" >>"${OUT}"

echo "Wrote ${OUT} (mode 600). Presence: DATABASE_URL=$([[ -n "${_db}" ]] && echo ok || echo missing) DEVNEST_S3_SNAPSHOT_BUCKET=$([[ -n "${_bucket}" ]] && echo ok || echo missing) AWS_REGION=$([[ -n "${_region}" ]] && echo ok || echo missing) DEVNEST_SNAPSHOT_STORAGE_PROVIDER=${_provider}"
echo "OAuth (no secrets): DEVNEST_FRONTEND_PUBLIC_BASE_URL=$([[ -n "${_fe}" ]] && echo set || echo missing) GITHUB_OAUTH_PUBLIC_BASE_URL=$([[ -n "${_gh_pub}" ]] && echo set || echo missing) GCLOUD_OAUTH_PUBLIC_BASE_URL=$([[ -n "${_gc_pub}" ]] && echo set || echo missing) OAUTH_GITHUB_CLIENT_ID=$([[ -n "${_ogh_id}" ]] && echo set || echo missing) OAUTH_GITHUB_CLIENT_SECRET=$([[ -n "${_ogh_sec}" ]] && echo set || echo missing) OAUTH_GOOGLE_CLIENT_ID=$([[ -n "${_ogo_id}" ]] && echo set || echo missing) OAUTH_GOOGLE_CLIENT_SECRET=$([[ -n "${_ogo_sec}" ]] && echo set || echo missing)"

unset _db _bucket _region _prefix _provider _ak _sk _fe _gh_pub _gc_pub _ogh_id _ogh_sec _ogo_id _ogo_sec || true
