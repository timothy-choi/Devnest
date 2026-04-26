import type { NextApiRequest, NextApiResponse } from "next";

import { backendRequest, readBackendJson } from "@/lib/server/backend-client";
import { sendMethodNotAllowed } from "@/lib/server/http";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  const workspaceId = Number(req.query.id);

  if (!Number.isFinite(workspaceId)) {
    res.status(400).json({ detail: "Invalid workspace id" });
    return;
  }

  if (req.method !== "GET") {
    sendMethodNotAllowed(res, ["GET"]);
    return;
  }

  const rawSid = req.query.snapshot_id;
  const sid = typeof rawSid === "string" && /^\d+$/.test(rawSid) ? rawSid : "";
  const query = sid ? `?snapshot_id=${encodeURIComponent(sid)}` : "";

  const response = await backendRequest({
    req,
    res,
    path: `/workspaces/${workspaceId}/snapshots/archive-download${query}`,
    accept: "application/json",
  });

  const data = await readBackendJson(response);
  if (!response.ok) {
    res.status(response.status).json(data ?? { detail: "Download metadata failed" });
    return;
  }

  res.status(200).json(data);
}
