import type { NextApiRequest, NextApiResponse } from "next";

import { backendRequest, readBackendJson } from "@/lib/server/backend-client";
import { forwardJson, sendMethodNotAllowed } from "@/lib/server/http";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  const workspaceId = Number(req.query.id);

  if (!Number.isFinite(workspaceId)) {
    res.status(400).json({ detail: "Invalid workspace id" });
    return;
  }

  if (req.method === "GET") {
    const response = await backendRequest({
      req,
      res,
      path: `/workspaces/${workspaceId}/snapshots`,
    });
    const data = await readBackendJson(response);
    forwardJson(res, response.status, data);
    return;
  }

  if (req.method === "POST") {
    const raw = (req.body || {}) as { name?: string; description?: string; metadata?: Record<string, unknown> };
    const name =
      (raw.name || "").trim() || `Save ${new Date().toISOString().slice(0, 19).replace("T", " ")}`;
    const response = await backendRequest({
      req,
      res,
      path: `/workspaces/${workspaceId}/snapshots`,
      method: "POST",
      body: {
        name,
        description: (raw.description || "").trim() || null,
        metadata: raw.metadata ?? null,
      },
    });
    const data = await readBackendJson(response);
    forwardJson(res, response.status, data);
    return;
  }

  sendMethodNotAllowed(res, ["GET", "POST"]);
}
