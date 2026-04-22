import type { NextApiRequest, NextApiResponse } from "next";
import { serialize } from "cookie";

import { backendRequest, readBackendJson } from "@/lib/server/backend-client";
import { sendMethodNotAllowed } from "@/lib/server/http";

type OAuthStartPayload = {
  authorization_url: string;
};

const OAUTH_RETURN_COOKIE = "devnest_oauth_return_to";

function authRouteFromSource(source: string | string[] | undefined) {
  return source === "signup" ? "/signup" : "/login";
}

function setOAuthReturnCookie(res: NextApiResponse, route: string) {
  res.setHeader(
    "Set-Cookie",
    serialize(OAUTH_RETURN_COOKIE, route, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.AUTH_COOKIE_SECURE === "true",
      path: "/",
      maxAge: 60 * 10,
    }),
  );
}

function redirectToAuthWithError(res: NextApiResponse, route: string, detail: string) {
  res.redirect(302, `${route}?oauth_error=${encodeURIComponent(detail)}`);
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== "GET") {
    sendMethodNotAllowed(res, ["GET"]);
    return;
  }

  const provider = typeof req.query.provider === "string" ? req.query.provider : "";
  const authRoute = authRouteFromSource(req.query.source);
  if (!provider) {
    redirectToAuthWithError(res, authRoute, "Unsupported OAuth provider.");
    return;
  }
  setOAuthReturnCookie(res, authRoute);

  let response: Awaited<ReturnType<typeof backendRequest>>;
  let data: OAuthStartPayload | { detail?: string } | null;
  try {
    response = await backendRequest({
      req,
      res,
      path: `/auth/oauth/${encodeURIComponent(provider)}`,
      method: "POST",
      authenticated: false,
      retryOnUnauthorized: false,
    });
    data = await readBackendJson<OAuthStartPayload | { detail?: string }>(response);
  } catch (err) {
    const message = err instanceof Error ? err.message : "unknown error";
    redirectToAuthWithError(
      res,
      authRoute,
      `Sign-in service could not be reached (${message}). If the app runs in Docker Compose, set INTERNAL_API_BASE_URL (e.g. http://backend:8000) on the frontend service.`,
    );
    return;
  }

  if (!response.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data && typeof data.detail === "string"
        ? data.detail
        : "Unable to start OAuth right now.";
    redirectToAuthWithError(res, authRoute, detail);
    return;
  }

  const authorizationUrl = (data as OAuthStartPayload).authorization_url?.trim();
  if (!authorizationUrl) {
    redirectToAuthWithError(res, authRoute, "OAuth provider did not return an authorization URL.");
    return;
  }

  res.redirect(302, authorizationUrl);
}
