import type { NextApiRequest, NextApiResponse } from "next";

import { readBackendJson, backendRequest } from "@/lib/server/backend-client";
import { forwardJson, sendMethodNotAllowed } from "@/lib/server/http";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  const workspaceId = Number(req.query.id);

  if (!Number.isFinite(workspaceId)) {
    res.status(400).json({ detail: "Invalid workspace id" });
    return;
  }

  if (req.method !== "POST") {
    sendMethodNotAllowed(res, ["POST"]);
    return;
  }

  const response = await backendRequest({
    req,
    res,
    path: `/workspaces/stop/${workspaceId}`,
    method: "POST",
  });
  const data = await readBackendJson(response);

  forwardJson(res, response.status, data);
}
