import type { NextApiRequest, NextApiResponse } from "next";

import { backendRequest, readBackendJson } from "@/lib/server/backend-client";
import { sendMethodNotAllowed } from "@/lib/server/http";

type OAuthStartPayload = {
  authorization_url: string;
};

function redirectToLoginWithError(res: NextApiResponse, detail: string) {
  res.redirect(302, `/login?oauth_error=${encodeURIComponent(detail)}`);
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== "GET") {
    sendMethodNotAllowed(res, ["GET"]);
    return;
  }

  const provider = typeof req.query.provider === "string" ? req.query.provider : "";
  if (!provider) {
    redirectToLoginWithError(res, "Unsupported OAuth provider.");
    return;
  }

  const response = await backendRequest({
    req,
    res,
    path: `/auth/oauth/${encodeURIComponent(provider)}`,
    method: "POST",
    authenticated: false,
    retryOnUnauthorized: false,
  });

  const data = await readBackendJson<OAuthStartPayload | { detail?: string }>(response);

  if (!response.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data && typeof data.detail === "string"
        ? data.detail
        : "Unable to start OAuth right now.";
    redirectToLoginWithError(res, detail);
    return;
  }

  const authorizationUrl = (data as OAuthStartPayload).authorization_url?.trim();
  if (!authorizationUrl) {
    redirectToLoginWithError(res, "OAuth provider did not return an authorization URL.");
    return;
  }

  res.redirect(302, authorizationUrl);
}
