export type WorkspaceStatus = "setting-up" | "running" | "stopped" | "restarting" | "error";

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
};
