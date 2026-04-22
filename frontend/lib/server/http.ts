import type { NextApiResponse } from "next";

import { getSetCookieHeaderValues } from "@/lib/server/response-cookies";

export function sendMethodNotAllowed(res: NextApiResponse, allowed: string[]) {
  res.setHeader("Allow", allowed);
  res.status(405).json({ detail: "Method not allowed" });
}

export function forwardJson(res: NextApiResponse, status: number, body: unknown) {
  res.status(status).json(body);
}

/** Copy ``Set-Cookie`` from an upstream fetch ``Response`` onto the Next.js API response. */
export function forwardSetCookieHeaders(res: NextApiResponse, upstream: Response) {
  const lines = getSetCookieHeaderValues(upstream.headers);
  if (!lines.length) {
    return;
  }
  for (const cookie of lines) {
    res.appendHeader("Set-Cookie", cookie);
  }
}
