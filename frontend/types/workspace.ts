export type WorkspaceStatus = "setting-up" | "running" | "stopped" | "restarting" | "error";

/** Control-plane assessment of on-disk project layout vs snapshots (GET /workspaces/{id}). */
export type ProjectDataLifecycle = "ok" | "unknown" | "restore_required" | "unrecoverable";

export type Workspace = {
  id: number;
  name: string;
  description: string;
  status: WorkspaceStatus;
  rawStatus: string;
  statusLabel: string;
  statusDetail: string | null;
  lastOpenedLabel: string;
  lastModifiedLabel: string;
  pendingAction: string | null;
  isBusy: boolean;
  canOpen: boolean;
  canStart: boolean;
  canStop: boolean;
  canRestart: boolean;
  canDelete: boolean;
  /** Control-plane reopen blockers (stale host, missing project dir, legacy paths). */
  reopenIssues?: string[];
  /** AVAILABLE snapshots count from GET /workspaces/{id} (restore when project data is missing). */
  restorableSnapshotCount?: number;
  /** True when persisted project data is missing (restore or remove). */
  projectDirectoryMissing?: boolean;
  projectDataLifecycle?: ProjectDataLifecycle;
  /** Short hint from the API; avoid showing raw reopen_issues on cards. */
  projectDataUserMessage?: string | null;
};
