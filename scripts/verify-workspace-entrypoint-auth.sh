#!/usr/bin/env bash
# Verify devnest-workspace-entrypoint.sh writes code-server auth config and preserves explicit CLI auth.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="${TMPDIR:-/tmp}/devnest-workspace-entrypoint-auth-$$"

cleanup() {
  rm -rf "${TMP}"
}
trap cleanup EXIT

mkdir -p "${TMP}/bin" "${TMP}/home/coder/.config" "${TMP}/home/coder/.local/share/code-server/extensions"
ln -s "${ROOT}/docker/workspace-entrypoint.sh" "${TMP}/entrypoint-under-test"
cat > "${TMP}/bin/entrypoint.sh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" > "${DEVNEST_TEST_ARGV_FILE}"
EOF
chmod 755 "${TMP}/bin/entrypoint.sh"

(
  cd "${TMP}"
  HOME="${TMP}/home/coder" \
  PATH="${TMP}/bin:${PATH}" \
  DEVNEST_TEST_ARGV_FILE="${TMP}/argv.txt" \
  DEVNEST_WORKSPACE_HOME="${TMP}/home/coder" \
  DEVNEST_CODE_SERVER_ENTRYPOINT="${TMP}/bin/entrypoint.sh" \
  DEVNEST_WORKSPACE_AUTH_MODE=none \
  CODE_SERVER_AUTH=none \
  bash "${TMP}/entrypoint-under-test" --auth password --bind-addr 0.0.0.0:8080 /home/coder/project
)

CFG="${TMP}/home/coder/.config/code-server/config.yaml"
grep -q '^auth: none$' "${CFG}"
grep -q '^bind-addr: 0.0.0.0:8080$' "${CFG}"
grep -q '^cert: false$' "${CFG}"
grep -q '^--auth none --bind-addr 0.0.0.0:8080 /home/coder/project$' "${TMP}/argv.txt"

echo "OK: workspace entrypoint writes auth: none config and starts code-server with --auth none."
