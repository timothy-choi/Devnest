import { ApiError, parseApiResponse } from "@/lib/api/error";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    credentials: "same-origin",
  });

  return parseApiResponse<T>(response);
}

export type AuthUser = {
  userAuthId: number;
  username: string;
  email: string;
  createdAt: string;
  displayName: string;
  avatarUrl: string | null;
  profileLoaded: boolean;
};

export type WorkspaceRecord = {
  id: number;
  name: string;
  description: string;
  status: string;
  lastOpenedLabel: string;
  lastModifiedLabel: string;
  createdAt: string;
  updatedAt: string;
  lastStarted: string | null;
  lastStopped: string | null;
  activeSessionsCount: number;
  statusReason: string | null;
  lastErrorMessage: string | null;
  /** Present when the control plane detected host/path drift vs current settings. */
  reopenIssues?: string[];
  restorableSnapshotCount?: number;
  projectDataLifecycle?: "ok" | "unknown" | "restore_required" | "unrecoverable";
  projectDataUserMessage?: string | null;
};

export type WorkspaceDetail = {
  id: number;
  name: string;
  description: string;
  status: string;
  lastOpenedLabel: string;
  lastModifiedLabel: string;
  createdAt: string;
  updatedAt: string;
  lastStarted: string | null;
  lastStopped: string | null;
  activeSessionsCount: number;
  statusReason: string | null;
  lastErrorMessage: string | null;
  /** Snake_case when proxied directly from FastAPI; camelCase when mapped via list API. */
  reopen_issues?: string[];
  reopenIssues?: string[];
  restorable_snapshot_count?: number;
  project_data_lifecycle?: "ok" | "unknown" | "restore_required" | "unrecoverable";
  project_data_user_message?: string | null;
};

export type WorkspaceAttachResponse = {
  accepted?: boolean;
  gateway_url?: string | null;
  issues?: string[];
  detail?: string;
};

export type NotificationRecord = {
  notificationId: number;
  notificationRecipientId: number;
  type: string;
  title: string;
  body: string;
  priority: string;
  recipientStatus: string;
  readAt: string | null;
  dismissedAt: string | null;
  createdAt: string;
};

export type NotificationPreferenceRecord = {
  preferenceId: number;
  notificationType: string;
  inAppEnabled: boolean;
  emailEnabled: boolean;
  pushEnabled: boolean;
  createdAt: string;
  updatedAt: string;
};

export type LoginInput = {
  username: string;
  password: string;
};

export type SignupInput = {
  username: string;
  email: string;
  password: string;
};

export type CreateWorkspaceInput = {
  name: string;
  repositoryUrl?: string;
  aiProvider?: "openai" | "anthropic" | "";
  aiApiKey?: string;
  aiModel?: string;
};

export type CreateSnapshotInput = {
  name: string;
  description?: string | null;
  metadata?: Record<string, unknown> | null;
};

export type CreateSnapshotAccepted = {
  workspace_id: number;
  snapshot_id: number;
  job_id: number;
  status: string;
};

/** GET /workspaces/{id}/snapshots — snake_case from FastAPI. */
export type SnapshotListItem = {
  workspace_snapshot_id: number;
  workspace_id: number;
  name: string;
  description: string | null;
  status: string;
  size_bytes: number | null;
  storage_backend: "s3" | "local" | "pending" | "unknown";
  created_at: string;
  metadata: Record<string, unknown> | null;
};

export type SaveNotificationPreferencesInput = {
  preferences: Array<{
    notificationType: string;
    inAppEnabled: boolean;
    emailEnabled: boolean;
    pushEnabled: boolean;
  }>;
};

export type SignupSuccess = {
  message: string;
  username: string;
  email: string;
};

export const browserApi = {
  auth: {
    async me() {
      return request<{ user: AuthUser | null }>("/api/auth/me", { method: "GET" });
    },
    async login(payload: LoginInput) {
      return request<{ user: AuthUser }>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    },
    async signup(payload: SignupInput) {
      return request<SignupSuccess>("/api/auth/signup", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    },
    async logout() {
      return request<{ message: string }>("/api/auth/logout", { method: "POST" });
    },
  },
  workspaces: {
    async list() {
      return request<{ items: WorkspaceRecord[]; total: number }>("/api/workspaces", {
        method: "GET",
      });
    },
    async get(id: number) {
      return request<WorkspaceDetail>(`/api/workspaces/${id}`, {
        method: "GET",
      });
    },
    async attach(id: number) {
      return request<WorkspaceAttachResponse>(`/api/workspaces/${id}/attach`, {
        method: "POST",
        body: JSON.stringify({}),
      });
    },
    async create(payload: CreateWorkspaceInput) {
      return request<{ workspace: WorkspaceRecord; message: string }>("/api/workspaces", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    },
    async stop(id: number) {
      return request<{ message: string }>(`/api/workspaces/${id}/stop`, {
        method: "POST",
      });
    },
    async restart(id: number) {
      return request<{ message: string }>(`/api/workspaces/${id}/restart`, {
        method: "POST",
      });
    },
    async remove(id: number) {
      return request<{ message: string }>(`/api/workspaces/${id}`, {
        method: "DELETE",
      });
    },
    async createSnapshot(id: number, payload: CreateSnapshotInput) {
      return request<CreateSnapshotAccepted>(`/api/workspaces/${id}/snapshots`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
    },
    async listSnapshots(id: number) {
      return request<SnapshotListItem[]>(`/api/workspaces/${id}/snapshots`, {
        method: "GET",
      });
    },
  },
  notifications: {
    async list(filterMode: "all" | "unread" | "read" = "all", limit = 20) {
      return request<{ items: NotificationRecord[]; total: number }>(
        `/api/notifications?filterMode=${filterMode}&limit=${limit}`,
        {
          method: "GET",
        },
      );
    },
    async getPreferences() {
      return request<{ preferences: NotificationPreferenceRecord[] }>("/api/notifications/preferences", {
        method: "GET",
      });
    },
    async savePreferences(payload: SaveNotificationPreferencesInput) {
      return request<{ preferences: NotificationPreferenceRecord[] }>("/api/notifications/preferences", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
    },
    async markReadBulk(notificationIds: number[]) {
      return request<{ items: NotificationRecord[] }>("/api/notifications/read-bulk", {
        method: "PUT",
        body: JSON.stringify({ notificationIds }),
      });
    },
  },
};

export function isUnauthorizedError(error: unknown) {
  return error instanceof ApiError && error.status === 401;
}
