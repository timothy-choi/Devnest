# DevNest — code-server Workspace Runtime

This document explains how DevNest provisions, configures, and maintains code-server within
each workspace container. It covers the image, entrypoint, ports, environment variables,
persistence mounts, readiness probing, and failure semantics.

---

## Overview

Every DevNest workspace runs **[code-server](https://github.com/coder/code-server)** — the
open-source VS Code server — inside a Docker container. From the user's perspective, opening
a workspace is equivalent to opening VS Code in a browser pointed at their personal project
directory.

code-server is chosen because:
- It exposes the full VS Code experience over HTTP (no Electron / local install required).
- Extensions, themes, and keyboard shortcuts are identical to VS Code.
- It exposes a single HTTP port (`:8080` by default) suitable for reverse-proxy access.

---

## Container Image Requirements

The workspace container image **must** include code-server. DevNest does not bundle code-server
as a binary into the API process — it relies on the workspace image having it installed and
launchable as the default entrypoint or `CMD`.

### Recommended image

```
FROM codercom/code-server:latest
```

The official `codercom/code-server` image starts code-server on port `8080` by default, runs
as the `coder` user, and respects these environment variables out of the box.

Override the image per workspace via:
- `DEVNEST_WORKSPACE_IMAGE` environment variable (process-level default)
- `runtime.image` field in `CreateWorkspaceRequest` (per-workspace override, stored in `WorkspaceConfig.config_json`)

### Custom images

Custom images must satisfy:
1. Start code-server listening on `WORKSPACE_IDE_CONTAINER_PORT` (`:8080`) when the container is launched.
2. Expose port `8080` in the `Dockerfile` (`EXPOSE 8080`).
3. Honor `CODE_SERVER_AUTH`, `PORT`, and `CS_DISABLE_GETTING_STARTED_OVERRIDE` environment variables.

---

## Injected Environment Variables

The orchestrator injects the following environment variables into every workspace container at
bring-up time (`DefaultOrchestratorService._code_server_env`):

| Variable | Value | Purpose |
|---|---|---|
| `CODE_SERVER_AUTH` | `none` | Disables code-server's built-in password auth. DevNest sessions handle auth via the gateway ForwardAuth flow. |
| `PORT` | `8080` | In-container listen port (matches `WORKSPACE_IDE_CONTAINER_PORT`). |
| `CS_DISABLE_GETTING_STARTED_OVERRIDE` | `1` | Suppresses the welcome/getting-started page on first launch. |

Per-workspace `env` overrides from `config_json.env` are merged on top of these defaults
(user-supplied values win). This allows advanced users to configure editor behaviour while
DevNest auth defaults remain in place.

---

## Port Mapping

| Port | Location | Purpose |
|---|---|---|
| `8080` | In-container | code-server listens here |
| Ephemeral host port | Docker host | Assigned by the engine when host-publish is configured; optional |

DevNest attaches workspaces to a bridge network so the API/gateway can reach the container
via its private IP at port `8080` — no host-port binding is required for the internal routing
path.

---

## Persistence Bind Mounts

Three bind mounts are created for every workspace container:

### 1. Project mount (required)

| Host path | Container path | Purpose |
|---|---|---|
| `<workspace_projects_base>/<wid>/` | `/home/coder/project` | User's primary workspace files |

This is the only **required** mount. All code-server sessions open the `/home/coder/project`
directory by default.

### 2. code-server config mount (optional, automatic)

| Host path | Container path | Purpose |
|---|---|---|
| `<workspace_projects_base>/ws-<wid>/code-server/config/` | `/home/coder/.config/code-server` | code-server YAML config, auth tokens |

### 3. code-server data mount (optional, automatic)

| Host path | Container path | Purpose |
|---|---|---|
| `<workspace_projects_base>/ws-<wid>/code-server/data/` | `/home/coder/.local/share/code-server` | Extensions, editor state, workspace history |

Both optional mounts are created automatically by the orchestrator at bring-up if
`DEVNEST_WORKSPACE_PROJECTS_BASE` is configured. They are **persistent across stop/start/restart**:
stopping and restarting a workspace will remount the same host directories, so extensions,
settings, and installed packages are preserved.

Set `DEVNEST_WORKSPACE_PROJECTS_BASE` in production:

```env
DEVNEST_WORKSPACE_PROJECTS_BASE=/data/devnest-workspaces
```

---

## Readiness Probe

The probe runner checks code-server readiness via:

1. **Container state check** (`check_container_running`): verifies the Docker container is in
   `running` state.
2. **Topology check** (`check_topology_state`): verifies the workspace has an allocated IP and
   is attached to the bridge network.
3. **Service reachability** (`check_service_reachable`): TCP connect to the workspace IP at
   port `8080`.
4. **HTTP readiness** (`check_service_http`, when `DEVNEST_WORKSPACE_HTTP_PROBE_ENABLED=true`):
   HTTP GET to `http://<workspace_ip>:8080/`. Responses with status **below 400** (2xx/3xx) count as
   ready so redirects are accepted.

A workspace transitions to `RUNNING` only when all checks pass (TCP plus HTTP when enabled). If
code-server fails to become ready within the probe timeout, the workspace enters `ERROR` state
and the reconcile loop will retry.

Set `DEVNEST_WORKSPACE_HTTP_PROBE_ENABLED=false` only in special environments where the API host
cannot HTTP-reach workspace container IPs (e.g. some automated test stacks). **Keep it true in
production** so `RUNNING` means the IDE is actually serving HTTP, not merely holding a TCP listensocket.

---

## Workspace Feature Gating

Terminal access (WebSocket) inside the workspace container is an **optional feature** that must
be explicitly enabled in the workspace config:

```json
{
  "features": {
    "terminal_enabled": true
  }
}
```

When `terminal_enabled` is `false` (default), the `/workspaces/{id}/terminal` WebSocket endpoint
rejects connections with code `4001` (policy violation). This prevents terminal access from
becoming an unintended admin shell.

---

## Gateway Access Flow

1. User opens `https://<workspace-id>.app.devnest.local` in browser.
2. Traefik ForwardAuth checks `/internal/gateway/auth` on the DevNest API.
3. API validates the workspace session token and workspace status.
4. On success, Traefik proxies the request to `<workspace_ip>:8080` (code-server).
5. code-server serves the VS Code UI.

---

## Failure Scenarios

| Scenario | Behavior |
|---|---|
| code-server fails to start | Container exits; probe fails; workspace enters `ERROR` |
| Port 8080 not bound (wrong image) | TCP probe times out; workspace enters `ERROR` after retries |
| `CODE_SERVER_AUTH` overridden to `password` | code-server requires a password; gateway pass-through still works but user may be prompted |
| Persistence bind mount host dir missing | Orchestrator creates it; no failure |
| Extension install fails | Extensions are stored in the data bind mount; next start resumes from partial state |

---

## Operations

### Check workspace container logs

```bash
docker logs devnest-ws-<workspace_id>
```

### Inspect bind mounts

```bash
docker inspect devnest-ws-<workspace_id> --format '{{json .Mounts}}'
```

### Pre-warm extension cache

Copy an extension directory to `<workspace_projects_base>/ws-<id>/code-server/data/extensions/`
before first start.

### Custom code-server config

Mount a pre-written `config.yaml` to `/home/coder/.config/code-server/config.yaml`. This file
is within the config bind mount path, so it persists across stop/start.
