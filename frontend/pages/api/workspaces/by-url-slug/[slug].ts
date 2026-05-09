import type { NextApiRequest, NextApiResponse } from "next";

import { readBackendJson, backendRequest } from "@/lib/server/backend-client";
import { forwardJson, sendMethodNotAllowed } from "@/lib/server/http";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  const slug = typeof req.query.slug === "string" ? req.query.slug.trim() : "";
  if (!slug) {
    res.status(400).json({ detail: "Invalid workspace slug" });
    return;
  }

  if (req.method === "GET") {
    const encoded = encodeURIComponent(slug);
    const response = await backendRequest({
      req,
      res,
      path: `/workspaces/by-url-slug/${encoded}`,
    });
    const data = await readBackendJson(response);
    forwardJson(res, response.status, data);
    return;
  }

  sendMethodNotAllowed(res, ["GET"]);
}
