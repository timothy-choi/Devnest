#!/usr/bin/env bash
# Serves a tiny static response on port 8080 so ws-123.app.devnest.local can be tested end-to-end.
# Run on the Docker host (not inside the Traefik container). Stop with Ctrl+C.
#
# Alternative: run code-server or any HTTP server on 8080 instead.

set -euo pipefail
PORT="${DEVNEST_WORKSPACE_EXAMPLE_UPSTREAM_PORT:-8080}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCROOT="${ROOT}/scripts/example-upstream-root"
mkdir -p "${DOCROOT}"
echo "<!DOCTYPE html><html><body><h1>DevNest gateway V1 upstream</h1><p>ws-123 → host:${PORT}</p></body></html>" > "${DOCROOT}/index.html"

echo "Serving ${DOCROOT} on http://127.0.0.1:${PORT} (Traefik ws-123.app.devnest.local → here)"
cd "${DOCROOT}"
exec python3 -m http.server "${PORT}"
