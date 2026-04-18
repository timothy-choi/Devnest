#!/usr/bin/env bash
# Verify devnest/workspace image metadata matches Dockerfile.workspace (entrypoint/cmd + revision label).
# Usage: ./scripts/verify-workspace-image.sh [IMAGE_REF]
# Exit 0 if checks pass, 1 otherwise.

set -euo pipefail

IMG="${1:-devnest/workspace:latest}"

if ! docker image inspect "$IMG" >/dev/null 2>&1; then
  echo "ERROR: image not found: ${IMG}" >&2
  echo "Build it: docker compose -f docker-compose.integration.yml build workspace-image" >&2
  exit 1
fi

echo "=== ${IMG} ==="
echo -n "Label devnest.workspace.dockerfile.revision: "
docker image inspect "$IMG" --format '{{index .Config.Labels "devnest.workspace.dockerfile.revision"}}'
echo
echo -n "Config.Entrypoint: "
docker image inspect "$IMG" --format '{{json .Config.Entrypoint}}'
echo
echo -n "Config.Cmd: "
docker image inspect "$IMG" --format '{{json .Config.Cmd}}'
echo

CMD=$(docker image inspect "$IMG" --format '{{json .Config.Cmd}}')
EP_LEN=$(docker image inspect "$IMG" --format '{{len .Config.Entrypoint}}')

# Expected from current Dockerfile.workspace: ENTRYPOINT is a single element (entrypoint.sh only).
FAIL=0
if [[ "${EP_LEN}" != "1" ]]; then
  echo "FAIL: Config.Entrypoint has ${EP_LEN} elements (expected 1). Stale image from before ENTRYPOINT reset?" >&2
  FAIL=1
fi
if echo "$CMD" | grep -q '"code-server"'; then
  echo 'FAIL: Cmd still contains "code-server" — image is NOT from the fixed CMD (stale build?).' >&2
  FAIL=1
fi

if [[ "$FAIL" -eq 0 ]]; then
  echo "OK: Entrypoint/Cmd look like the post-fix Dockerfile.workspace."
fi
exit "$FAIL"
