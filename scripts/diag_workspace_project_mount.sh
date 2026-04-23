#!/usr/bin/env bash
# Compare persisted workspace project files on the Docker host vs inside the running container,
# plus git index/worktree hints. Read-only; does not modify files.
#
# Usage:
#   WORKSPACE_ID=7 \
#   HOST_PROJECT_DIR=/var/lib/devnest/workspace-projects/7-<project_storage_key> \
#   ./scripts/diag_workspace_project_mount.sh [REL_PATH]
#
# REL_PATH is optional, relative to the project root (e.g. src/foo.py). When set, prints
# host/container existence and basic stat when available.
set -euo pipefail

WORKSPACE_ID="${WORKSPACE_ID:-}"
HOST_PROJECT_DIR="${HOST_PROJECT_DIR:-}"

if [[ -z "$WORKSPACE_ID" || -z "$HOST_PROJECT_DIR" ]]; then
  echo "Set WORKSPACE_ID and HOST_PROJECT_DIR (absolute path to the bind-mount source on the Docker host)." >&2
  exit 1
fi

REL="${1:-}"

echo "=== DevNest workspace project mount diagnostic ==="
echo "workspace_id=$WORKSPACE_ID"
echo "host_project_dir=$HOST_PROJECT_DIR"
echo "container_path=/home/coder/project"
[[ -n "$REL" ]] && echo "relative_path=$REL"
echo

if [[ ! -d "$HOST_PROJECT_DIR" ]]; then
  echo "ERROR: HOST_PROJECT_DIR is not a directory on this host: $HOST_PROJECT_DIR" >&2
  exit 1
fi

echo "--- Host: top-level listing (max 50 entries) ---"
ls -la "$HOST_PROJECT_DIR" | head -n 50
echo

if [[ -n "$REL" ]]; then
  hp="$HOST_PROJECT_DIR/$REL"
  echo "--- Host: target path ---"
  if [[ -e "$hp" ]]; then
    ls -la "$hp"
  else
    echo "(missing) $hp"
  fi
  echo
fi

cid="$(docker ps -q -f "label=devnest.workspace_id=$WORKSPACE_ID" | head -n 1 || true)"
if [[ -z "$cid" ]]; then
  echo "WARNING: No running container with label devnest.workspace_id=$WORKSPACE_ID." >&2
  echo "  Host checks above still show persisted files. Start the workspace to compare inside the container." >&2
  exit 0
fi

echo "--- Container: docker id (full) ---"
docker inspect -f '{{.Id}}' "$cid"
echo "--- Container: mounts containing /home/coder/project ---"
docker inspect -f '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}' "$cid" | grep -F '/home/coder/project' || true
echo

echo "--- Container: top-level /home/coder/project (max 50) ---"
docker exec "$cid" sh -lc 'ls -la /home/coder/project | head -n 50'
echo

if [[ -n "$REL" ]]; then
  echo "--- Container: target path ---"
  docker exec "$cid" sh -lc "if test -e \"/home/coder/project/$REL\"; then ls -la \"/home/coder/project/$REL\"; else echo \"(missing) /home/coder/project/$REL\"; fi"
  echo
fi

if docker exec "$cid" sh -lc 'test -d /home/coder/project/.git' 2>/dev/null; then
  echo "--- Git (container, /home/coder/project): status --porcelain=v1 (first 80 lines) ---"
  (docker exec "$cid" sh -lc 'cd /home/coder/project && git status --porcelain=v1' || true) | head -n 80
  echo
  if [[ -n "$REL" ]]; then
    echo "--- Git: ls-files / rev-parse (for REL_PATH) ---"
    docker exec "$cid" sh -lc "cd /home/coder/project && git ls-files -- \"$REL\" 2>/dev/null || true; if git rev-parse --verify HEAD:\"\$REL\" >/dev/null 2>&1; then echo 'HEAD has blob for path'; else echo 'HEAD: no such path'; fi" || true
  fi
else
  echo "--- Git: no .git under /home/coder/project (skip git diagnostics) ---"
fi

echo
echo "Interpretation:"
echo "  - If a path is missing on BOTH host and container: file is not on the bind-mounted disk (true loss or never created)."
echo "  - If present on host but missing in container: abnormal (wrong container, bind not applied, or rare Docker issue)."
echo "  - If git shows 'D path': deleted in worktree vs last commit (recover via git checkout -- path if still tracked)."
echo "  - If path missing but 'git ls-files' lists it: deleted file may be recoverable with git checkout -- <path>."
