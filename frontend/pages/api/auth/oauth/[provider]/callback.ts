import type { NextApiRequest, NextApiResponse } from "next";
import { serialize } from "cookie";

import { backendRequest, readBackendJson } from "@/lib/server/backend-client";
import { clearAuthCookies, setAuthCookies } from "@/lib/server/auth-cookies";
import { sendMethodNotAllowed } from "@/lib/server/http";
import { getSetCookieHeaderValues } from "@/lib/server/response-cookies";

type OAuthCallbackPayload = {
  access_token: string;
};

const OAUTH_RETURN_COOKIE = "devnest_oauth_return_to";

function extractCookieValue(setCookieHeaders: string[] | undefined, cookieName: string): string | null {
  for (const header of setCookieHeaders || []) {
    const match = header.match(new RegExp(`(?:^|\\s)${cookieName}=([^;]+)`));
    if (match?.[1]) {
      return decodeURIComponent(match[1]);
    }
  }
  return null;
}

function resolveAuthReturnRoute(req: NextApiRequest) {
  return req.cookies[OAUTH_RETURN_COOKIE] === "/signup" ? "/signup" : "/login";
}

function clearOAuthReturnCookie(res: NextApiResponse) {
  res.appendHeader(
    "Set-Cookie",
    serialize(OAUTH_RETURN_COOKIE, "", {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.AUTH_COOKIE_SECURE === "true",
      path: "/",
      maxAge: 0,
    }),
  );
}

function redirectToAuthWithError(req: NextApiRequest, res: NextApiResponse, detail: string) {
  clearAuthCookies(res);
  clearOAuthReturnCookie(res);
  res.redirect(302, `${resolveAuthReturnRoute(req)}?oauth_error=${encodeURIComponent(detail)}`);
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
    redirectToAuthWithError(req, res, "OAuth callback is missing required parameters.");
    return;
  }

  const query = new URLSearchParams({ code, state }).toString();
  let response: Awaited<ReturnType<typeof backendRequest>>;
  let data: OAuthCallbackPayload | { detail?: string } | null;
  try {
    response = await backendRequest({
      req,
      res,
      path: `/auth/oauth/${encodeURIComponent(provider)}/callback?${query}`,
      method: "GET",
      authenticated: false,
      retryOnUnauthorized: false,
    });
    data = await readBackendJson<OAuthCallbackPayload | { detail?: string }>(response);
  } catch (err) {
    const message = err instanceof Error ? err.message : "unknown error";
    redirectToAuthWithError(
      req,
      res,
      `Sign-in service could not be reached (${message}). If the app runs in Docker Compose, the frontend needs INTERNAL_API_BASE_URL (e.g. http://backend:8000).`,
    );
    return;
  }

  if (!response.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data && typeof data.detail === "string"
        ? data.detail
        : "OAuth sign-in failed.";
    redirectToAuthWithError(req, res, detail);
    return;
  }

  const accessToken = (data as OAuthCallbackPayload).access_token?.trim();
  let refreshToken: string | null = null;
  try {
    refreshToken = extractCookieValue(getSetCookieHeaderValues(response.headers), "refresh_token");
  } catch {
    redirectToAuthWithError(req, res, "OAuth sign-in completed, but the refresh cookie could not be read.");
    return;
  }

  if (!accessToken || !refreshToken) {
    redirectToAuthWithError(req, res, "OAuth sign-in completed, but the session could not be established.");
    return;
  }

  setAuthCookies(res, {
    accessToken,
    refreshToken,
  });
  clearOAuthReturnCookie(res);
  res.redirect(302, "/dashboard");
}
