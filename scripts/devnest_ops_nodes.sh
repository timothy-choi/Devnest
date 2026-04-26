#!/usr/bin/env bash
# DevNest ops helper: internal execution-node API (list, workspaces, drain, undrain).
# Requires: curl, jq (optional for pretty print).
#
#   export DEVNEST_API_BASE="http://localhost:8000"
#   export INTERNAL_API_KEY="your-infrastructure-key"
#   ./scripts/devnest_ops_nodes.sh list
#   ./scripts/devnest_ops_nodes.sh workspaces
#   ./scripts/devnest_ops_nodes.sh drain '{"node_key":"node-2"}'
#   ./scripts/devnest_ops_nodes.sh undrain '{"node_key":"node-2"}'

set -euo pipefail

BASE="${DEVNEST_API_BASE:-http://localhost:8000}"
BASE="${BASE%/}"
KEY="${INTERNAL_API_KEY:-}"

if [[ -z "$KEY" ]]; then
  echo "error: set INTERNAL_API_KEY (infrastructure-scoped internal API key)" >&2
  exit 1
fi

HDR=(-H "X-Internal-API-Key: ${KEY}" -H "Content-Type: application/json")

cmd="${1:-}"
shift || true

case "$cmd" in
  list)
    if command -v jq >/dev/null 2>&1; then
      curl -sS "${BASE}/internal/execution-nodes/" "${HDR[@]}" | jq .
    else
      curl -sS "${BASE}/internal/execution-nodes/" "${HDR[@]}"
    fi
    ;;
  workspaces)
    qs=""
    if [[ -n "${1:-}" ]]; then
      qs="?limit_per_node=${1}"
    fi
    if command -v jq >/dev/null 2>&1; then
      curl -sS "${BASE}/internal/execution-nodes/workspaces-by-node${qs}" "${HDR[@]}" | jq .
    else
      curl -sS "${BASE}/internal/execution-nodes/workspaces-by-node${qs}" "${HDR[@]}"
    fi
    ;;
  drain)
    body="${1:?usage: drain JSON body e.g. {\"node_key\":\"node-2\"}}"
    curl -sS -X POST "${BASE}/internal/execution-nodes/drain" "${HDR[@]}" -d "${body}"
    echo
    ;;
  undrain)
    body="${1:?usage: undrain JSON body e.g. {\"node_key\":\"node-2\"}}"
    curl -sS -X POST "${BASE}/internal/execution-nodes/undrain" "${HDR[@]}" -d "${body}"
    echo
    ;;
  *)
    echo "usage: $0 list | workspaces [limit_per_node] | drain '<json>' | undrain '<json>'" >&2
    exit 2
    ;;
esac
