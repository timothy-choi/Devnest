import type { NextApiRequest, NextApiResponse } from "next";

import { backendRequest, readBackendJson } from "@/lib/server/backend-client";
import { clearAuthCookies, setAuthCookies } from "@/lib/server/auth-cookies";
import { sendMethodNotAllowed } from "@/lib/server/http";

type OAuthCallbackPayload = {
  access_token: string;
};

function extractCookieValue(setCookieHeaders: string[] | undefined, cookieName: string): string | null {
  for (const header of setCookieHeaders || []) {
    const match = header.match(new RegExp(`(?:^|\\s)${cookieName}=([^;]+)`));
    if (match?.[1]) {
      return decodeURIComponent(match[1]);
    }
  }
  return null;
}

function redirectToLoginWithError(res: NextApiResponse, detail: string) {
  clearAuthCookies(res);
  res.redirect(302, `/login?oauth_error=${encodeURIComponent(detail)}`);
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== "GET") {
    sendMethodNotAllowed(res, ["GET"]);
    return;
  }

  const provider = typeof req.query.provider === "string" ? req.query.provider : "";
  const code = typeof req.query.code === "string" ? req.query.code : "";
  const state = typeof req.query.state === "string" ? req.query.state : "";

  if (!provider || !code || !state) {
    redirectToLoginWithError(res, "OAuth callback is missing required parameters.");
    return;
  }

  const query = new URLSearchParams({ code, state }).toString();
  const response = await backendRequest({
    req,
    res,
    path: `/auth/oauth/${encodeURIComponent(provider)}/callback?${query}`,
    method: "GET",
    authenticated: false,
    retryOnUnauthorized: false,
  });
  const data = await readBackendJson<OAuthCallbackPayload | { detail?: string }>(response);

  if (!response.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data && typeof data.detail === "string"
        ? data.detail
        : "OAuth sign-in failed.";
    redirectToLoginWithError(res, detail);
    return;
  }

  const accessToken = (data as OAuthCallbackPayload).access_token?.trim();
  const refreshToken = extractCookieValue(response.headers.raw()["set-cookie"], "refresh_token");

  if (!accessToken || !refreshToken) {
    redirectToLoginWithError(res, "OAuth sign-in completed, but the session could not be established.");
    return;
  }

  setAuthCookies(res, {
    accessToken,
    refreshToken,
  });
  res.redirect(302, "/dashboard");
}
