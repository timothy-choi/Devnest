import type { NextApiRequest, NextApiResponse } from "next";

import { readBackendJson, backendRequest } from "@/lib/server/backend-client";
import { sendMethodNotAllowed, forwardJson } from "@/lib/server/http";

type BackendWorkspaceListItem = {
  workspace_id: number;
  name: string;
  status: string;
  is_private: boolean;
  created_at: string;
};

type BackendWorkspaceListResponse = {
  items: BackendWorkspaceListItem[];
  total: number;
};

type BackendWorkspaceDetail = {
  workspace_id: number;
  name: string;
  description: string | null;
  status: string;
  status_reason: string | null;
  last_error_message: string | null;
  active_sessions_count: number;
  created_at: string;
  updated_at: string;
  last_started: string | null;
  last_stopped: string | null;
  reopen_issues?: string[];
  restorable_snapshot_count?: number;
};

function mapDetail(detail: BackendWorkspaceDetail) {
  return {
    id: detail.workspace_id,
    name: detail.name,
    description: detail.description || "",
    status: detail.status,
    lastOpenedLabel: detail.last_started || detail.created_at,
    lastModifiedLabel: detail.updated_at,
    createdAt: detail.created_at,
    updatedAt: detail.updated_at,
    lastStarted: detail.last_started,
    lastStopped: detail.last_stopped,
    activeSessionsCount: detail.active_sessions_count,
    statusReason: detail.status_reason,
    lastErrorMessage: detail.last_error_message,
    reopenIssues: detail.reopen_issues ?? [],
    restorableSnapshotCount: detail.restorable_snapshot_count ?? 0,
  };
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method === "GET") {
    const listResponse = await backendRequest({
      req,
      res,
      path: "/workspaces",
    });

    const listData = await readBackendJson<BackendWorkspaceListResponse | { detail: string }>(listResponse);

    if (!listResponse.ok) {
      forwardJson(res, listResponse.status, listData);
      return;
    }

    const list = listData as BackendWorkspaceListResponse;

    const detailResults = await Promise.all(
      list.items.map(async (item: BackendWorkspaceListItem) => {
        const detailResponse = await backendRequest({
          req,
          res,
          path: `/workspaces/${item.workspace_id}`,
        });

        if (!detailResponse.ok) {
          return null;
        }

        const detail = await readBackendJson<BackendWorkspaceDetail>(detailResponse);
        return mapDetail(detail);
      }),
    );

    res.status(200).json({
      items: detailResults.filter(Boolean),
      total: list.total,
    });
    return;
  }

  if (req.method === "POST") {
    const body = req.body as {
      name: string;
      repositoryUrl?: string;
      aiProvider?: "openai" | "anthropic" | "";
      aiApiKey?: string;
      aiModel?: string;
    };

    const provider = (body.aiProvider || "").trim();
    const aiApiKey = (body.aiApiKey || "").trim();
    const aiModel = (body.aiModel || "").trim();
    const runtimeEnv: Record<string, string> = {};

    if (provider === "openai") {
      runtimeEnv.DEVNEST_AI_DEFAULT_PROVIDER = "openai";
      runtimeEnv.OPENAI_MODEL = aiModel || "gpt-4.1-mini";
    } else if (provider === "anthropic") {
      runtimeEnv.DEVNEST_AI_DEFAULT_PROVIDER = "anthropic";
      runtimeEnv.ANTHROPIC_MODEL = aiModel || "claude-3-5-sonnet-latest";
    }

    const createResponse = await backendRequest({
      req,
      res,
      path: "/workspaces",
      method: "POST",
      body: {
        name: body.name,
        description: body.repositoryUrl
          ? `Repository seed requested: ${body.repositoryUrl}`
          : undefined,
        runtime: {
          env: runtimeEnv,
          features: {
            terminal_enabled: true,
            ci_enabled: false,
            ai_tools_enabled: true,
          },
        },
        ai_secret: provider && aiApiKey ? { provider, api_key: aiApiKey } : undefined,
      },
    });

    const createData = await readBackendJson<{ workspace_id: number } | { detail: string }>(createResponse);

    if (!createResponse.ok) {
      forwardJson(res, createResponse.status, createData);
      return;
    }

    const accepted = createData as { workspace_id: number };

    const detailResponse = await backendRequest({
      req,
      res,
      path: `/workspaces/${accepted.workspace_id}`,
    });
    const detail = await readBackendJson<BackendWorkspaceDetail | { detail: string }>(detailResponse);

    if (!detailResponse.ok) {
      forwardJson(res, detailResponse.status, detail);
      return;
    }

    res.status(200).json({
      message: "Workspace creation accepted.",
      workspace: mapDetail(detail as BackendWorkspaceDetail),
    });
    return;
  }

  sendMethodNotAllowed(res, ["GET", "POST"]);
}
