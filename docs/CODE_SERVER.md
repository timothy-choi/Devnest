# DevNest — code-server Workspace Runtime

This document explains how DevNest provisions, configures, and maintains code-server within
each workspace container. It covers the image, entrypoint, ports, environment variables,
persistence mounts, readiness probing, and failure semantics.

---

## Readiness and health checks

When `DEVNEST_WORKSPACE_HTTP_PROBE_ENABLED=true` (production default), bring-up and health checks
perform **TCP** to the workspace IDE port, then **HTTP GET** to a code-server-specific path
(default **`DEVNEST_WORKSPACE_IDE_HEALTH_PATH=/healthz`**) so `RUNNING` implies the IDE endpoint
answers with 2xx/3xx, not merely an open port. On EC2/VM deployments where the control plane is
not co-located with Docker, TCP/HTTP probes run on the execution node via
`service_reachability_runner` (SSH/SSM) when `devnest_probe_assume_colocated_engine=false`.

**Staging/production contract:** Startup requires `DEVNEST_REQUIRE_IDE_HTTP_PROBE=true` and HTTP
probing enabled, so **TCP-only “healthy” is never accepted** in those environments. Development may
set `DEVNEST_REQUIRE_IDE_HTTP_PROBE=false` (and optionally disable HTTP probes) for tests where
workspace IPs are not routable from the API host.

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

### Browser URL (`?folder=` / `?workspace=`)

**Root cause (code-server, not DevNest routing):** On `GET /` with no `folder` or `workspace` query, code-server resolves which folder or `.code-workspace` file to open from (1) persisted “last opened” settings, or (2) **CLI positional arguments** (the trailing path you pass after `code-server`). If either yields a folder or workspace file, the server issues an **HTTP redirect** so the browser’s URL includes `?folder=` or `?workspace=`. That logic lives in upstream [`src/node/routes/vscode.ts`](https://github.com/coder/code-server/blob/main/src/node/routes/vscode.ts) (see the `router.get("/", …)` handler around the `NO_FOLDER_OR_WORKSPACE_QUERY` / `HAS_FOLDER_OR_WORKSPACE_FROM_CLI` branches).

In this repo, **`Dockerfile.workspace`** sets `CMD ["--auth", "none", "--bind-addr", "0.0.0.0:8080", "/home/coder/project"]`. **`docker/workspace-entrypoint.sh`** forwards those arguments (after normalizing `--auth`) to the upstream `entrypoint.sh` → `code-server`. The final positional **`/home/coder/project`** is exactly what triggers the redirect to `/?folder=/home/coder/project` on first visit to `/`.

**`config.yaml`:** The entrypoint only writes `bind-addr`, `auth`, and `cert`. There is **no** supported setting there to “open this folder by default but keep the location bar at `/`”.

**CLI flags:** Upstream exposes [`ignore-last-opened`](https://github.com/coder/code-server/blob/main/src/node/cli.ts) (open an empty window instead of last opened). There is **no** documented flag equivalent to “default folder without `?folder=` in the URL.” Removing the CLI folder argument would avoid that particular redirect only until “last opened” is persisted again—then the same redirect behavior applies from stored query state.

**Why client-side URL cleanup is brittle:** A `history.replaceState` hack in `workbench.html` can fail because the VS Code web workbench may **reconcile the URL** with internal workbench state (re-applying query parameters), because the **redirect already committed** the canonical URL before the SPA loads, and because paths and CSP differ across code-server / VS Code versions.

**Traefik / reverse-proxy rewrites:** Stripping query parameters from responses or rewriting `Location` on redirects risks **redirect loops** (browser returns to `/` → code-server redirects again) or breaking behavior that assumes `folder` / `workspace` appear in the URL (reload, deep links). DevNest does **not** rely on proxy tricks for this.

**Verdict:** A stable “external URL always `https://ws-…/` with no query while still opening `/home/coder/project` by default” is **not** achievable without **forking or changing upstream code-server** (or accepting trade-offs such as no CLI default folder and no last-opened persistence). Treat **`?folder=`** as **normal, intentional code-server behavior** when a folder is opened from the server side.

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
| `<workspace_projects_base>/<wid>/code-server/config/` | `/home/coder/.config/code-server` | code-server YAML config, auth tokens |

### 3. code-server data mount (optional, automatic)

| Host path | Container path | Purpose |
|---|---|---|
| `<workspace_projects_base>/<wid>/code-server/data/` | `/home/coder/.local/share/code-server` | Extensions, editor state, workspace history |

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

Copy an extension directory to `<workspace_projects_base>/<id>/code-server/data/extensions/`
before first start.

### Custom code-server config

Mount a pre-written `config.yaml` to `/home/coder/.config/code-server/config.yaml`. This file
is within the config bind mount path, so it persists across stop/start.
