# DevNest demo script

Use this as a **verbatim checklist** when showing the project to recruiters or interviewers. Times assume a warm machine and network; first workspace start can take one to two minutes (image pull + code-server cold start).

**Prerequisites:** Docker, repo cloned, `.env.integration` created from [`.env.integration.example`](../.env.integration.example). For **local** demo, defaults in the example file are enough. For **EC2 + RDS + S3**, fill database, public URLs, OAuth (optional), S3 bucket/region, and expect flags per [INTEGRATION_STARTUP.md](./INTEGRATION_STARTUP.md).

---

## 0. Start the stack

From the **repository root**:

```bash
cp -n .env.integration.example .env.integration
./scripts/deploy_integration.sh
```

Confirm:

- Script exits zero.
- `curl -sf http://localhost:8000/ready` returns JSON with database ok (adjust host/port if you changed them).

Open the UI: `DEVNEST_FRONTEND_PUBLIC_BASE_URL` from your env (default [http://localhost:3000](http://localhost:3000)).

---

## 1. Create an account and sign in

1. Open the app in the browser.
2. **Register** a new user (email + password) **or** use **GitHub / Google** if OAuth env vars are set in `.env.integration` and provider apps are configured.
3. Confirm you reach the **dashboard** (workspace list).

---

## 2. Create a workspace

1. Click **create workspace** (or equivalent primary action).
2. Wait until the workspace shows **RUNNING** (the UI polls; SSE may also update the card).

If the card stalls, check `docker compose -f docker-compose.integration.yml logs -f workspace-worker` for job errors.

---

## 3. Open the workspace (code-server)

1. Click **Open workspace** on the card.
2. Expect a redirect to the **gateway URL** (`ws-<id>.<DEVNEST_BASE_DOMAIN>` with the configured scheme/port).
3. Confirm the **code-server** UI loads.

**Local tip:** `app.lvh.me` subdomains resolve to `127.0.0.1` on the same host as Docker. Remote demos require `DEVNEST_BASE_DOMAIN` to resolve to the Traefik host from the **audience’s** machine.

---

## 4. Create or edit a file

1. In code-server, create a small file, e.g. `DEMO.txt`, with a memorable line: `DevNest demo <ISO-date>`.
2. Save the file inside the workspace project tree (default project path in the IDE).

---

## 5. Save a snapshot (“Save workspace”)

1. Return to the **dashboard** (same browser session).
2. Click **Save workspace** on that workspace card.
3. Wait until the button returns to normal and/or snapshot count updates (worker completes `SNAPSHOT_CREATE`).

If save stays pending, verify the workspace is **RUNNING** or an allowed state for export, and inspect worker logs.

---

## 6. Download the workspace archive

1. On the same card, click **Download workspace** (enabled when at least one restorable snapshot exists).
2. Confirm the browser downloads a `.tar.gz` file.

**API equivalent (with user JWT):** `GET /workspaces/{id}/snapshots/archive` returns the archive (latest AVAILABLE snapshot by default). Use the same auth you use in the UI.

---

## 7. Verify the snapshot in S3 (cloud posture only)

Skip this section when `DEVNEST_SNAPSHOT_STORAGE_PROVIDER` is **local** (default for bundled Postgres).

With **S3** enabled, objects follow:

`s3://<DEVNEST_S3_SNAPSHOT_BUCKET>/<DEVNEST_S3_SNAPSHOT_PREFIX>/ws-<workspace_id>/snapshot-<snapshot_id>.tar.gz`

1. Note `workspace_id` from the dashboard URL or API.
2. List snapshots: `GET /workspaces/{id}/snapshots` (or infer `snapshot_id` from DB/UI if exposed).
3. On a host with AWS credentials for the bucket:

   ```bash
   aws s3 ls "s3://${DEVNEST_S3_SNAPSHOT_BUCKET}/${DEVNEST_S3_SNAPSHOT_PREFIX}/ws-<workspace_id>/" --region "${AWS_REGION}"
   ```

4. Confirm `snapshot-<id>.tar.gz` exists and non-zero size.

---

## 8. Restart the stack and reopen

1. From repo root:

   ```bash
   docker compose --env-file .env.integration -f docker-compose.integration.yml restart
   ```

   For a **cold** demo, use `down` then `./scripts/deploy_integration.sh` again.

2. Reload the UI, sign in again if the session expired.
3. Open the **same** workspace. Project files live on the host bind mount, so your `DEMO.txt` should still be there after a control-plane restart; if the UI shows the workspace stopped, start it again, then open the IDE. If the file is missing, re-create it in the IDE (and ensure you saved the file in code-server, not only an unsaved buffer).

Optional narrative: **live data** lives on `WORKSPACE_PROJECTS_BASE` bind mounts; **snapshots** are archives used for download/restore, with S3 as the multi-node store when configured.

---

## Demo checklist (copy/paste)

- [ ] `./scripts/deploy_integration.sh` succeeds; `/ready` ok
- [ ] Register or OAuth login → dashboard
- [ ] Create workspace → **RUNNING**
- [ ] **Open workspace** → code-server loads via gateway URL
- [ ] Create/edit file with obvious content → save in IDE
- [ ] **Save workspace** → snapshot completes
- [ ] **Download workspace** → `.tar.gz` received
- [ ] (S3 only) `aws s3 ls` shows `snapshot-*.tar.gz` under `ws-<id>/`
- [ ] `compose restart` (or full redeploy) → reopen workspace → data story matches expectations
