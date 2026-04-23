#!/usr/bin/env bash
# Preinstall code-server extensions for Dockerfile.workspace.
# Never fails the image build: OpenVSX / marketplace CDN outages and flaky CI must not break `docker build`.
set -uo pipefail

install_one() {
  local ext="$1"
  local label="${2:-extension}"
  if [[ -z "${ext// }" ]]; then
    return 0
  fi
  local attempt
  for attempt in 1 2 3 4 5 6; do
    if code-server --install-extension "$ext" 2>&1; then
      echo "[workspace-extensions] installed ${label}: ${ext}"
      return 0
    fi
    echo "[workspace-extensions] failed ${label}: ${ext} (attempt ${attempt}/6), sleeping 8s..."
    sleep 8
  done
  echo "[workspace-extensions] skipped after retries ${label}: ${ext}"
  return 0
}

# Default / high-value extensions (best-effort).
for ext in \
  ms-python.python \
  ms-toolsai.jupyter \
  dbaeumer.vscode-eslint \
  esbenp.prettier-vscode \
  github.vscode-github-actions; do
  install_one "$ext" "default"
done

# Optional (marketplace may not list these for code-server).
for ext in GitHub.copilot GitHub.copilot-chat Continue.continue; do
  install_one "$ext" "optional"
done

exit 0
