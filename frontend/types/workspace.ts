export type WorkspaceStatus = "setting-up" | "running" | "stopped" | "restarting" | "error";

export type Workspace = {
  id: string;
  name: string;
  description: string;
  status: WorkspaceStatus;
  lastOpenedLabel: string;
  lastModifiedLabel: string;
  repositoryUrl: string;
  features: {
    ciCd: boolean;
    aiTools: boolean;
    terminal: boolean;
  };
};
