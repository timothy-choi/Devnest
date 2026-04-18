import type { NextApiRequest, NextApiResponse } from "next";

import { readBackendJson, backendRequest } from "@/lib/server/backend-client";
import { forwardJson, forwardSetCookieHeaders, sendMethodNotAllowed } from "@/lib/server/http";

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
    path: `/workspaces/attach/${workspaceId}`,
    method: "POST",
    body: req.body && typeof req.body === "object" ? req.body : {},
  });
  const data = await readBackendJson(response);

  forwardSetCookieHeaders(res, response);
  forwardJson(res, response.status, data);
}
