#!/usr/bin/env bash
# Restore and validate the integration deploy environment.
#
# Source of truth on the deployment host:
#   ~/.devnest-env-integration.backup
#
# This script intentionally prints only presence/safe values. It does not dump the env file.

set -euo pipefail

BACKUP_FILE="${DEVNEST_ENV_BACKUP_FILE:-${HOME}/.devnest-env-integration.backup}"
TARGET_FILE="${DEVNEST_ENV_TARGET_FILE:-.env.integration}"

echo "[setup-env] backup env file: ${BACKUP_FILE}"
echo "[setup-env] target env file: ${TARGET_FILE}"

if [[ ! -f "${BACKUP_FILE}" ]]; then
  echo "[setup-env] ERROR: missing ${BACKUP_FILE}" >&2
  echo "[setup-env] Create it once on the deploy host from your production .env.integration, then rerun deploy." >&2
  exit 1
fi

cp "${BACKUP_FILE}" "${TARGET_FILE}"
chmod 600 "${TARGET_FILE}" 2>/dev/null || true
echo "[setup-env] restored ${TARGET_FILE} from backup"

set -a
# shellcheck disable=SC1090
source "${TARGET_FILE}"
set +a

missing=0
for key in DEVNEST_WORKSPACE_CONTAINER_IMAGE DEVNEST_EC2_AMI_ID DEVNEST_EC2_SUBNET_ID; do
  if [[ -z "${!key:-}" ]]; then
    echo "[setup-env] ERROR: required env var ${key} is missing or empty in ${TARGET_FILE}" >&2
    missing=1
  else
    echo "[setup-env] ${key}: set"
  fi
done

if [[ "${missing}" -ne 0 ]]; then
  exit 1
fi

echo "[setup-env] selected workspace image: ${DEVNEST_WORKSPACE_CONTAINER_IMAGE}"
if [[ "${DEVNEST_WORKSPACE_CONTAINER_IMAGE}" == "devnest/workspace:latest" ]]; then
  echo "[setup-env] WARN: using default local workspace image devnest/workspace:latest; EC2 nodes may need a registry-pullable image." >&2
fi

echo "[setup-env] environment validation OK"
