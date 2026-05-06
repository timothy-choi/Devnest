#!/usr/bin/env bash
# Shared helpers for scripts/chaos/*.sh (source with: source "$(dirname "$0")/common.sh")
# shellcheck shell=bash

set -euo pipefail

chaos_script_dir() {
  cd "$(dirname "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")" && pwd
}

chaos_repo_root() {
  cd "$(chaos_script_dir)/../.." && pwd
}

chaos_warn() {
  printf '[chaos] WARNING: %s\n' "$*" >&2
}

chaos_die() {
  printf '[chaos] ERROR: %s\n' "$*" >&2
  exit 1
}
