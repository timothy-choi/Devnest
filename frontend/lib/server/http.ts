import type { NextApiResponse } from "next";
import type { Response as NodeFetchResponse } from "node-fetch";

export function sendMethodNotAllowed(res: NextApiResponse, allowed: string[]) {
  res.setHeader("Allow", allowed);
  res.status(405).json({ detail: "Method not allowed" });
}

export function forwardJson(res: NextApiResponse, status: number, body: unknown) {
  res.status(status).json(body);
}

/** Copy ``Set-Cookie`` from a node-fetch upstream response onto the Next.js API response. */
export function forwardSetCookieHeaders(res: NextApiResponse, upstream: NodeFetchResponse) {
  const raw = upstream.headers.raw()["set-cookie"];
  if (!raw?.length) {
    return;
  }
  for (const cookie of raw) {
    res.appendHeader("Set-Cookie", cookie);
  }
}
