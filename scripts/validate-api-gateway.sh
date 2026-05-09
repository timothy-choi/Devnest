#!/usr/bin/env bash
# Validate DevNest API reachability via Traefik (HTTPS after LE is configured).
#
# Usage:
#   ./scripts/validate-api-gateway.sh https://api.devnest-app.com
#   DEVNEST_API_PUBLIC_HOST=api.devnest-app.com ./scripts/validate-api-gateway.sh http://EC2_PUBLIC_IP:9081
#
set -euo pipefail

BASE="${1:-https://api.devnest-app.com}"
BASE="${BASE%/}"
HOST_HEADER="${DEVNEST_API_PUBLIC_HOST:-api.devnest-app.com}"

CURL_HOST=()
if [[ "${BASE}" == http://* ]] && [[ "${BASE}" != *"${HOST_HEADER}"* ]]; then
  CURL_HOST=( -H "Host: ${HOST_HEADER}" )
fi

echo "=== HEAD ${BASE}/health ==="
curl -fsSI "${CURL_HOST[@]}" "${BASE}/health"

echo ""
echo "=== GET ${BASE}/health ==="
curl -fsS "${CURL_HOST[@]}" "${BASE}/health"
echo ""

echo "=== Optional: direct HTTPS to api.devnest-app.com (after DNS + Let's Encrypt) ==="
echo "# curl -fsSI https://api.devnest-app.com/health"
echo "# curl -fsS https://api.devnest-app.com/health"
