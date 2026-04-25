import type { NextApiRequest, NextApiResponse } from "next";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

import { backendRequest, readBackendJson } from "@/lib/server/backend-client";
import { sendMethodNotAllowed } from "@/lib/server/http";

export const config = {
  api: {
    responseLimit: false as const,
  },
};

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
    path: `/workspaces/${workspaceId}/snapshots/archive${query}`,
    accept: "*/*",
  });

  if (!response.ok) {
    const data = await readBackendJson(response);
    res.status(response.status).json(data ?? { detail: "Download failed" });
    return;
  }

  const ct = response.headers.get("content-type") || "application/gzip";
  const cd = response.headers.get("content-disposition");
  res.status(200);
  res.setHeader("Content-Type", ct);
  if (cd) {
    res.setHeader("Content-Disposition", cd);
  }

  if (!response.body) {
    res.end();
    return;
  }

  const nodeStream = Readable.fromWeb(response.body as import("stream/web").ReadableStream<Uint8Array>);
  await pipeline(nodeStream, res);
}
