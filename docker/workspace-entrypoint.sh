#!/usr/bin/env sh
set -eu

SEED_ROOT="/opt/devnest/code-server/extensions"
TARGET_ROOT="/home/coder/.local/share/code-server/extensions"

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

exec /usr/bin/entrypoint.sh "$@"
