# DevNest — Workspace Persistence Guarantees

This document describes what DevNest persists for a workspace, across which lifecycle events,
and what the operator must provide to make persistence work in production.

---

## What Is Persisted

| Layer | What | Where | Survives |
|---|---|---|---|
| **Project files** | All files under `/home/coder/project` | Host bind mount | stop, start, restart, reattach |
| **code-server config** | `config.yaml`, auth tokens, UI theme | Host bind mount | stop, start, restart |
| **code-server data** | Extensions, workspace history, editor state | Host bind mount | stop, start, restart |
| **Workspace metadata** | Name, status, description, owner | PostgreSQL `workspace` table | always |
| **Runtime placement** | `container_id`, node_id, topology_id | PostgreSQL `workspace_runtime` table | survives API restart |
| **Snapshots** | Compressed `.tar.gz` of project directory | Local FS or S3 | manual trigger |

---

## Persistence Across Lifecycle Events

| Event | Project Files | Extensions | Editor State | Config |
|---|---|---|---|---|
| Stop | ✅ Persisted (bind mount) | ✅ | ✅ | ✅ |
| Start after stop | ✅ Remounted | ✅ | ✅ | ✅ |
| Restart | ✅ Remounted | ✅ | ✅ | ✅ |
| Reattach (new session) | ✅ | ✅ | ✅ | ✅ |
| Update (new config version) | ✅ (restart-based, mounts preserved) | ✅ | ✅ | ✅ |
| Delete | ❌ Host dir remains but no auto-cleanup | ✅ (until manual cleanup) | — | — |
| Snapshot + Restore | ✅ Restored from archive | ❌ Not in snapshot | ❌ Not in snapshot | ❌ Not in snapshot |

---

## Host Directory Layout

For `DEVNEST_WORKSPACE_PROJECTS_BASE=/data/devnest-workspaces`:

```
/data/devnest-workspaces/
├── <workspace_id>/              # Primary project bind mount → /home/coder/project
│   ├── main.py
│   ├── README.md
│   └── ...
└── ws-<workspace_id>/
    └── code-server/
        ├── config/              # → /home/coder/.config/code-server
        │   └── config.yaml
        └── data/                # → /home/coder/.local/share/code-server
            ├── extensions/
            └── User/
```

---

## Configuration

Set the following environment variable on the API/worker process:

```env
DEVNEST_WORKSPACE_PROJECTS_BASE=/data/devnest-workspaces
```

- Must be an **absolute path** on the Docker host where the API/worker and Docker daemon both run.
- In multi-node deployments (EC2 workers), this path must exist and be writable on each execution node.
- In single-host local deployments, the path defaults to a system temp dir (not recommended for production).

---

## Snapshots

Snapshots are point-in-time compressed archives of `/home/coder/project`. They do **not** capture
extensions or editor state (only project files).

### Create a snapshot

```
POST /workspaces/{id}/snapshots
```

This enqueues a `SNAPSHOT_CREATE` job. The archive is stored in:
- **Local**: `<DEVNEST_SNAPSHOT_STORAGE_ROOT>/ws-<wid>/snapshot-<id>.tar.gz`
- **S3**: `s3://<bucket>/<prefix>/ws-<wid>/snapshot-<id>.tar.gz`

### Restore a snapshot

```
POST /workspaces/{id}/snapshots/{snapshot_id}/restore
```

Enqueues a `SNAPSHOT_RESTORE` job. Restore is **atomic**:
1. The archive is validated (format check, no absolute paths, no path traversal).
2. Files are extracted to a temporary sibling directory.
3. The workspace project directory is atomically swapped with the temp directory.
4. The backup of the original directory is removed on success.

If extraction fails (e.g. path traversal detected), the original directory is preserved intact.

### Safety guarantees

| Threat | Mitigation |
|---|---|
| Absolute path in archive | Rejected before extraction starts |
| `../` traversal path | Rejected before extraction starts |
| Device/special files | Rejected (chr/blk/fifo members) |
| Hard-link outside dest | Rejected |
| Partial restore on error | Atomic swap; original preserved on failure |
| Invalid archive format | `tarfile.is_tarfile()` check before open |

---

## Multi-Node Persistence

In multi-node deployments (local + EC2 execution nodes), workspace project directories exist
on the node where the workspace last ran. This means:

- **Within a node:** stop/start always remounts the same host directory. ✅
- **Cross-node migration:** if a workspace migrates to a different node, project files are
  not automatically transferred. Use snapshots to export from node A and import on node B
  (manual operator action in V1).

A shared network filesystem (NFS, EFS, etc.) mounted at the same path on all nodes eliminates
this limitation and is the recommended production setup for multi-node deployments.

---

## Deletion Behavior

When a workspace is deleted (`DELETE /workspaces/{id}`), the DevNest API:
1. Stops and removes the Docker container.
2. Marks the workspace `DELETED` in the database.
3. **Does not** remove the host bind mount directories.

This is intentional: project files are never automatically purged so operators can recover
data before an explicit cleanup run. A future garbage-collection job can remove host dirs for
DELETED workspaces older than a configurable retention period.
