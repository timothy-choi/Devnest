# DevNest Integrations Guide

This document describes the product-enabling backend integrations implemented in the
"Product-Enabling Backend Integrations" phase.

---

## Table of Contents

1. [GitHub OAuth — Sign-in and Repository Access](#1-github-oauth)
2. [Google OAuth — Sign-in](#2-google-oauth)
3. [Workspace Repository Import](#3-workspace-repository-import)
4. [Workspace Git Synchronization](#4-workspace-git-synchronization)
5. [Workspace CI/CD Triggers](#5-workspace-cicd-triggers)
6. [Workspace Terminal (WebSocket TTY)](#6-workspace-terminal-websocket-tty)
7. [Security Notes](#7-security-notes)
8. [Configuration Reference](#8-configuration-reference)
9. [Deferred Features](#9-deferred-features)

---

## 1. GitHub OAuth

DevNest supports two separate OAuth flows for GitHub:

### 1a. Sign-in Flow (existing)

Allows users to register/log in with their GitHub account.

**Endpoints:**
```
POST /auth/oauth/github           → { authorization_url }
GET  /auth/oauth/github/callback  → { access_token } + Set-Cookie: refresh_token
```

**Scopes requested:** `read:user user:email`

The provider access token is **not** persisted; it is used only to fetch the user profile during sign-up/login.

### 1b. Repository Access Connect Flow (new)

Allows an already-authenticated DevNest user to connect their GitHub account with extended scopes (`repo`) so they can clone private repositories and trigger CI/CD workflows.

**Endpoints:**
```
POST /auth/provider-tokens/github/connect
  → 200 { authorization_url, provider }

GET  /auth/provider-tokens/github/callback?code=...&state=...
  → 200 ProviderTokenResponse (token stored encrypted, scopes returned)

GET  /auth/provider-tokens
  → 200 [ ProviderTokenResponse, ... ]

DELETE /auth/provider-tokens/{token_id}
  → 204
```

**Scopes requested:** `read:user user:email repo`

The access token is encrypted with `DEVNEST_TOKEN_ENCRYPTION_KEY` (Fernet/AES-256) before
being written to `user_provider_token`. The plaintext token is only held in memory during
the callback request and is never logged.

**Setup:**
1. Create a GitHub OAuth App at https://github.com/settings/developers.
2. Set the callback URL to `{GITHUB_OAUTH_PUBLIC_BASE_URL}/auth/provider-tokens/github/callback`.
3. Configure `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, and `GITHUB_OAUTH_PUBLIC_BASE_URL`.
   In the integration/EC2 stack this should be the browser-visible frontend origin, not the backend API origin.
4. Generate and set `DEVNEST_TOKEN_ENCRYPTION_KEY`.

---

## 2. Google OAuth

Google is supported as a **sign-in provider only** in V1.

**Endpoints:**
```
POST /auth/oauth/google           → { authorization_url }
GET  /auth/oauth/google/callback  → { access_token } + Set-Cookie: refresh_token
```

**Scopes requested:** `openid email profile`

Repository access via Google is not applicable. GitLab/other providers are deferred.

**Setup:**
1. Create OAuth 2.0 credentials at https://console.developers.google.com/.
2. Set the authorized redirect URI to `{GCLOUD_OAUTH_PUBLIC_BASE_URL}/auth/oauth/google/callback`.
3. Configure `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GCLOUD_OAUTH_PUBLIC_BASE_URL`.
   In the integration/EC2 stack this should be the browser-visible frontend origin, not the backend API origin.

---

## 3. Workspace Repository Import

Clone a Git repository into a workspace container asynchronously.

**Endpoint:**
```
POST /workspaces/{workspace_id}/import-repo
Body: {
  "repo_url": "https://github.com/alice/myproject.git",
  "branch": "main",
  "clone_dir": "/workspace/myproject",    // optional; defaults to /workspace/project
  "use_provider": "github"                // optional; which stored provider token to use
}
→ 202 {
  "repo_id": 1,
  "workspace_id": 42,
  "repo_url": "...",
  "branch": "main",
  "clone_dir": "/workspace/myproject",
  "clone_status": "pending",
  "job_id": 17
}
```

**Flow:**
1. Creates a `WorkspaceRepository` row (`clone_status=pending`).
2. Enqueues a `REPO_IMPORT` worker job.
3. Returns 202 immediately.
4. The worker runs `git clone` inside the container:
   - For private repos, the stored GitHub token is injected via `GITHUB_TOKEN` env var (never in the command line).
   - Git is configured with `credential.helper` to read `GITHUB_TOKEN`.
5. `clone_status` is updated to `cloned` on success or `failed` with `error_msg` on failure.

**Status check:**
```
GET /workspaces/{workspace_id}/repo
→ 200 { "clone_status": "cloned", "last_synced_at": "...", ... }
```

**Remove association** (does not delete files in container):
```
DELETE /workspaces/{workspace_id}/repo
→ 204
```

Each workspace has at most **one** associated repository in V1. Delete the association before importing a different repo.

---

## 4. Workspace Git Synchronization

Run `git pull` or `git push` inside the running workspace container.

**The workspace must be in RUNNING state for these operations.**

### Pull

```
POST /workspaces/{workspace_id}/git/pull
Body: {
  "remote": "origin",           // default: "origin"
  "branch": "main",             // default: tracked branch from WorkspaceRepository
  "use_provider": "github"      // optional; which provider token to use for credentials
}
→ 200 {
  "success": true,
  "exit_code": 0,
  "output": "Already up to date.\n",
  "operation": "pull",
  "repo_url": "https://github.com/alice/myproject.git"
}
```

### Push

```
POST /workspaces/{workspace_id}/git/push
Body: {
  "remote": "origin",
  "branch": "main",
  "force": false,               // uses --force-with-lease if true
  "use_provider": "github"
}
→ 200 { "success": true, "exit_code": 0, "output": "...", "operation": "push" }
```

**Architecture:** Git operations are executed **inside the container** via `docker exec`. The workspace container must have `git` installed. Provider tokens are masked in all logs and API responses — `GitResult.output` never contains the raw token.

**Timeout:** 60 seconds. Returns 504 if exceeded.

---

## 5. Workspace CI/CD Triggers

Trigger GitHub Actions workflows via `repository_dispatch` events from a workspace.

### Configure CI

```
POST /workspaces/{workspace_id}/ci/config
Body: {
  "provider": "github_actions",
  "repo_owner": "myorg",
  "repo_name": "myrepo",
  "workflow_file": "ci.yml",    // informational only
  "default_branch": "main"
}
→ 201 CIConfigResponse
```

```
GET    /workspaces/{workspace_id}/ci/config   → 200 CIConfigResponse
DELETE /workspaces/{workspace_id}/ci/config   → 204
```

### Trigger a workflow

```
POST /workspaces/{workspace_id}/ci/trigger
Body: {
  "event_type": "devnest_trigger",   // GitHub event_type for repository_dispatch
  "ref": "main",                     // optional branch/tag override
  "inputs": { "key": "value" },      // optional payload
  "use_provider": "github"           // which provider token to use
}
→ 201 {
  "trigger_id": 5,
  "workspace_id": 42,
  "status": "triggered",
  "event_type": "devnest_trigger",
  "ref": "main",
  "triggered_at": "2026-04-12T..."
}
```

**Requirements:**
- User must have a stored GitHub provider token with `repo` scope (from the connect flow above).
- The GitHub repository must have a workflow with `on: repository_dispatch` configured.
- Example workflow:
  ```yaml
  on:
    repository_dispatch:
      types: [devnest_trigger]
  ```

### List trigger history

```
GET /workspaces/{workspace_id}/ci/triggers?limit=20
→ 200 [ CITriggerResponse, ... ]
```

Every trigger attempt (success or failure) is recorded in `ci_trigger_record` for auditing.

---

## 6. Workspace Terminal (WebSocket TTY)

Open an interactive terminal inside a running workspace container.

**Endpoint:**
```
WS /workspaces/{workspace_id}/terminal?token=<token>
```

**Authentication:**
The `token` query parameter accepts:
1. A DevNest **JWT access token** (from `/auth/login`) — workspace ownership is checked.
2. A **workspace session token** (`dnws_...` from `POST /workspaces/{id}/attach`) — session validity is checked.

The workspace must be in **RUNNING** state with a container assigned.

**Protocol:**

| Direction | Frame type | Content |
|---|---|---|
| Client → Server | binary | Raw stdin bytes (keystrokes, paste) |
| Client → Server | text JSON | `{"type":"resize","cols":200,"rows":50}` for PTY resize |
| Server → Client | binary | Raw stdout/stderr bytes |
| Server → Client | text JSON | `{"type":"error","message":"..."}` on fatal setup error |

**Close codes:**
- `4001` — Authentication / access denied (before upgrade)
- `1001` — Workspace not running or container not ready
- `1011` — Internal relay error

**xterm.js integration example:**
```javascript
const ws = new WebSocket(`wss://api.yourdomain.com/workspaces/${workspaceId}/terminal?token=${jwtToken}`);
const terminal = new Terminal();
terminal.open(document.getElementById('terminal'));
ws.binaryType = 'arraybuffer';
terminal.onData(data => ws.send(new TextEncoder().encode(data)));
ws.onmessage = e => terminal.write(new Uint8Array(e.data));
terminal.onResize(({ cols, rows }) => ws.send(JSON.stringify({ type: 'resize', cols, rows })));
```

**Execution modes:**

| Node mode | Terminal support | Notes |
|---|---|---|
| `local_docker` | ✅ Full PTY | Docker SDK `exec_create` + `exec_start(socket=True, tty=True)` |
| `ssh_docker` | ✅ Full PTY | Docker SDK over SSH URL (same API path) |
| `ssm_docker` | ❌ Not supported | Returns error message; use AWS Session Manager directly |

**Shell:** Configurable via `DEVNEST_WORKSPACE_SHELL` (default `/bin/bash`).

---

## 7. Security Notes

- **Token encryption:** Provider OAuth tokens are encrypted at rest with Fernet/AES-256. Set `DEVNEST_TOKEN_ENCRYPTION_KEY` to a dedicated random key in production. If unset, a key is derived from `JWT_SECRET_KEY` (warn-logged; not recommended).
- **No token leakage:** Provider tokens are never included in logs, API responses, or Git command arguments. `git credential.helper` is used to inject them via environment variable.
- **Terminal auth:** The WebSocket endpoint validates authentication **before** accepting the upgrade. Unauthenticated connections are rejected with close code 4001.
- **Workspace ownership:** All integration routes enforce workspace ownership. Other users receive 403/404.
- **Token scope isolation:** `user_provider_token` rows are user-scoped; a user cannot access another user's provider tokens.

---

## 8. Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `DEVNEST_TOKEN_ENCRYPTION_KEY` | `""` | Fernet key for encrypting provider tokens (derive from JWT_SECRET_KEY if empty; set in production!) |
| `GITHUB_CLIENT_ID` | `""` | GitHub OAuth App client ID |
| `GITHUB_CLIENT_SECRET` | `""` | GitHub OAuth App client secret |
| `GITHUB_OAUTH_PUBLIC_BASE_URL` | `""` | Public base URL for GitHub OAuth callbacks |
| `GOOGLE_CLIENT_ID` | `""` | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | `""` | Google OAuth client secret |
| `GCLOUD_OAUTH_PUBLIC_BASE_URL` | `""` | Public base URL for Google OAuth callbacks |
| `DEVNEST_WORKSPACE_SHELL` | `/bin/bash` | Shell launched in terminal sessions |
| `DEVNEST_TERMINAL_DEFAULT_COLS` | `200` | Default PTY column width |
| `DEVNEST_TERMINAL_DEFAULT_ROWS` | `50` | Default PTY row height |

---

## 9. Deferred Features

The following features are **intentionally deferred** for a later phase:

- **GitLab / Bitbucket integration** — GitHub is the only supported Git provider in V1.
- **Google provider token for repos** — Google is a sign-in provider only; not a Git provider.
- **SSH key-based git operations** — V1 uses HTTPS with OAuth tokens only.
- **Full CI/CD pipeline management** — V1 supports GitHub Actions `repository_dispatch` only.
- **Shared / collaborative terminals** — V1 is single-user per terminal session.
- **SSM interactive terminal** — AWS Session Manager supports this but requires a separate SDK flow.
- **Browser-based IDE** — Frontend/UI implementation deferred.
- **GitHub App integration** — V1 uses OAuth Apps only; GitHub App installation is deferred.
- **Advanced branch/PR workflows** — V1 supports basic push/pull on a single branch.
- **Enterprise SSO (SAML/OIDC)** — Deferred to an enterprise phase.
