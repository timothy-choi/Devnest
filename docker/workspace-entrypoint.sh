#!/usr/bin/env bash
set -euo pipefail

auth_mode="${DEVNEST_WORKSPACE_AUTH_MODE:-${CODE_SERVER_AUTH:-none}}"
auth_mode="$(printf '%s' "${auth_mode}" | tr '[:upper:]' '[:lower:]')"
workspace_id="${DEVNEST_WORKSPACE_ID:-}"
node_key="${DEVNEST_NODE_KEY:-}"
code_server_home="${DEVNEST_WORKSPACE_HOME:-/home/coder}"
code_server_config_dir="${code_server_home}/.config/code-server"
code_server_config="${code_server_config_dir}/config.yaml"
code_server_entrypoint="${DEVNEST_CODE_SERVER_ENTRYPOINT:-/usr/bin/entrypoint.sh}"

case "${auth_mode}" in
  none)
    unset PASSWORD || true
    unset DEVNEST_WORKSPACE_PASSWORD || true
    ;;
  password)
    if [ -z "${DEVNEST_WORKSPACE_PASSWORD:-}" ]; then
      echo "DEVNEST_WORKSPACE_AUTH_MODE=password requires DEVNEST_WORKSPACE_PASSWORD" >&2
      exit 64
    fi
    export PASSWORD="${DEVNEST_WORKSPACE_PASSWORD}"
    ;;
  *)
    echo "Unsupported DEVNEST_WORKSPACE_AUTH_MODE=${auth_mode}; expected none or password" >&2
    exit 64
    ;;
esac

mkdir -p "${code_server_config_dir}"
cat > "${code_server_config}" <<EOF
bind-addr: 0.0.0.0:8080
auth: ${auth_mode}
cert: false
EOF

echo "DevNest workspace starting: workspace_id=${workspace_id:-unknown} node_key=${node_key:-unknown} auth_mode=${auth_mode}"

code_server_args=()
skip_next_arg=false
for arg in "$@"; do
  if [ "${skip_next_arg}" = true ]; then
    skip_next_arg=false
    continue
  fi

  case "${arg}" in
    --auth)
      skip_next_arg=true
      ;;
    --auth=*)
      ;;
    *)
      code_server_args+=("${arg}")
      ;;
  esac
done

SEED_ROOT="/opt/devnest/code-server/extensions"
TARGET_ROOT="${code_server_home}/.local/share/code-server/extensions"

mkdir -p "${TARGET_ROOT}"

has_real_extensions() {
  find "${TARGET_ROOT}" -mindepth 1 -maxdepth 1 ! -name "extensions.json" -print -quit 2>/dev/null | grep -q .
}

if ! has_real_extensions && [ -d "${SEED_ROOT}" ]; then
  if find "${SEED_ROOT}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null | grep -q .; then
    cp -R "${SEED_ROOT}"/. "${TARGET_ROOT}"/
    echo "Seeded default code-server extensions into ${TARGET_ROOT}"
  fi
fi

exec "${code_server_entrypoint}" --auth "${auth_mode}" "${code_server_args[@]}"
