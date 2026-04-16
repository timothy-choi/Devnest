import type { NextApiResponse } from "next";

export function sendMethodNotAllowed(res: NextApiResponse, allowed: string[]) {
  res.setHeader("Allow", allowed);
  res.status(405).json({ detail: "Method not allowed" });
}

export function forwardJson(res: NextApiResponse, status: number, body: unknown) {
  res.status(status).json(body);
}
