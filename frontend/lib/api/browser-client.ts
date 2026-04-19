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
};

export type WorkspaceAttachResponse = {
  accepted?: boolean;
  gateway_url?: string | null;
  issues?: string[];
  detail?: string;
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
  enableCiCd: boolean;
  enableAiTools: boolean;
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
  },
};

export function isUnauthorizedError(error: unknown) {
  return error instanceof ApiError && error.status === 401;
}
