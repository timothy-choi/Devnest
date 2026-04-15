import type { NextApiRequest, NextApiResponse } from "next";

import { readBackendJson, backendRequest } from "@/lib/server/backend-client";
import { clearAuthCookies, getRefreshTokenCookieName } from "@/lib/server/auth-cookies";
import { forwardJson, sendMethodNotAllowed } from "@/lib/server/http";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== "POST") {
    sendMethodNotAllowed(res, ["POST"]);
    return;
  }

  const refreshToken = req.cookies[getRefreshTokenCookieName()];

  if (refreshToken) {
    const response = await backendRequest({
      req,
      res,
      path: "/auth/logout",
      method: "POST",
      body: {
        refresh_token: refreshToken,
      },
      authenticated: false,
      retryOnUnauthorized: false,
    });
    const data = await readBackendJson(response);

    clearAuthCookies(res);
    forwardJson(res, response.ok ? 200 : response.status, data);
    return;
  }

  clearAuthCookies(res);
  res.status(200).json({ message: "Logged out" });
}
