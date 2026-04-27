#!/usr/bin/env bash
# DevNest ops helper: internal execution-node API (list, workspaces, drain, undrain, deregister).
# Requires: curl, jq (optional for pretty print).
#
#   export DEVNEST_API_BASE="http://localhost:8000"
#   export INTERNAL_API_KEY="your-infrastructure-key"
#   ./scripts/devnest_ops_nodes.sh list
#   ./scripts/devnest_ops_nodes.sh workspaces
#   ./scripts/devnest_ops_nodes.sh drain '{"node_key":"<NODE_KEY>"}'
#   ./scripts/devnest_ops_nodes.sh undrain '{"node_key":"<NODE_KEY>"}'
#   ./scripts/devnest_ops_nodes.sh deregister '{"node_key":"<NODE_KEY>"}'
#   ./scripts/devnest_ops_nodes.sh heartbeat '{"node_key":"<NODE_KEY>","docker_ok":true,"disk_free_mb":50000,"slots_in_use":0,"version":"phase3b"}'
#   # With jq: export NODE_KEY=<your_execution_node_key>  (required when omitting JSON arg)
#   ./scripts/devnest_ops_nodes.sh heartbeat
#   ./scripts/devnest_ops_nodes.sh smoke '{"node_key":"<NODE_KEY>","read_only_command":"docker_info"}'

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
  deregister)
    body="${1:?usage: deregister JSON body e.g. {\"node_key\":\"...\"} or {\"node_id\":2}}"
    curl -sS -X POST "${BASE}/internal/execution-nodes/deregister" "${HDR[@]}" -d "${body}"
    echo
    ;;
  heartbeat)
    if [[ -n "${1:-}" ]]; then
      body="$1"
    else
      if ! command -v jq >/dev/null 2>&1; then
        echo "usage: $0 heartbeat '<json>'  (or install jq and set NODE_KEY when omitting JSON)" >&2
        exit 2
      fi
      nk="${NODE_KEY:?set NODE_KEY for heartbeat (e.g. export NODE_KEY=your-node-key)}"
      ver="${HEARTBEAT_VERSION:-devnest-ops-heartbeat}"
      disk="${DISK_FREE_MB:-}"
      slots="${SLOTS_IN_USE:-0}"
      docker_ok="${DOCKER_OK:-true}"
      if [[ -n "$disk" ]]; then
        body="$(jq -n \
          --arg nk "$nk" \
          --argjson docker_ok "$docker_ok" \
          --argjson disk "$disk" \
          --argjson slots "$slots" \
          --arg ver "$ver" \
          '{node_key:$nk, docker_ok:$docker_ok, disk_free_mb:$disk, slots_in_use:$slots, version:$ver}')"
      else
        body="$(jq -n \
          --arg nk "$nk" \
          --argjson docker_ok "$docker_ok" \
          --argjson slots "$slots" \
          --arg ver "$ver" \
          '{node_key:$nk, docker_ok:$docker_ok, slots_in_use:$slots, version:$ver}')"
      fi
    fi
    if command -v jq >/dev/null 2>&1; then
      curl -sS -X POST "${BASE}/internal/execution-nodes/heartbeat" "${HDR[@]}" -d "${body}" | jq .
    else
      curl -sS -X POST "${BASE}/internal/execution-nodes/heartbeat" "${HDR[@]}" -d "${body}"
      echo
    fi
    ;;
  smoke)
    if [[ -n "${1:-}" ]]; then
      body="$1"
    else
      if ! command -v jq >/dev/null 2>&1; then
        echo "usage: $0 smoke '<json>'  (or install jq and set NODE_KEY when omitting JSON)" >&2
        exit 2
      fi
      nk="${NODE_KEY:?set NODE_KEY for smoke (e.g. export NODE_KEY=your-node-key)}"
      roc="${SMOKE_READ_ONLY_COMMAND:-docker_info}"
      body="$(jq -n --arg nk "$nk" --arg roc "$roc" '{node_key:$nk, read_only_command:$roc}')"
    fi
    if command -v jq >/dev/null 2>&1; then
      curl -sS -X POST "${BASE}/internal/execution-nodes/smoke-read-only" "${HDR[@]}" -d "${body}" | jq .
    else
      curl -sS -X POST "${BASE}/internal/execution-nodes/smoke-read-only" "${HDR[@]}" -d "${body}"
      echo
    fi
    ;;
  *)
    echo "usage: $0 list | workspaces [limit_per_node] | drain '<json>' | undrain '<json>' | deregister '<json>' | heartbeat '<json>' | smoke '<json>'" >&2
    exit 2
    ;;
esac
