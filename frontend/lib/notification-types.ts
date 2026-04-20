export const MANAGED_WORKSPACE_NOTIFICATION_TYPES = [
  { type: "workspace.create.succeeded", label: "Workspace created" },
  { type: "workspace.create.failed", label: "Workspace creation failed" },
  { type: "workspace.stop.succeeded", label: "Workspace stopped" },
  { type: "workspace.stop.failed", label: "Workspace stop failed" },
  { type: "workspace.restart.succeeded", label: "Workspace restarted" },
  { type: "workspace.restart.failed", label: "Workspace restart failed" },
  { type: "workspace.delete.succeeded", label: "Workspace deleted" },
  { type: "workspace.delete.failed", label: "Workspace delete failed" },
] as const;

export type ManagedWorkspaceNotificationType = (typeof MANAGED_WORKSPACE_NOTIFICATION_TYPES)[number]["type"];
