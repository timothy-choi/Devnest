import type { NextApiRequest, NextApiResponse } from "next";

import { readBackendJson, backendRequest } from "@/lib/server/backend-client";
import {
  clearAuthCookies,
  getAccessTokenCookieName,
  getRefreshTokenCookieName,
} from "@/lib/server/auth-cookies";
import { forwardJson, sendMethodNotAllowed } from "@/lib/server/http";

type AuthProfile = {
  user_auth_id: number;
  username: string;
  email: string;
  created_at: string;
};

type MyProfile = {
  display_name: string;
  avatar_url: string | null;
};

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== "GET") {
    sendMethodNotAllowed(res, ["GET"]);
    return;
  }

  const hasAccessToken = Boolean(req.cookies[getAccessTokenCookieName()]);
  const hasRefreshToken = Boolean(req.cookies[getRefreshTokenCookieName()]);

  if (!hasAccessToken && !hasRefreshToken) {
    res.status(200).json({ user: null });
    return;
  }

  const authResponse = await backendRequest({
    req,
    res,
    path: "/auth",
  });

  if (!authResponse.ok) {
    clearAuthCookies(res);
    if (authResponse.status === 401) {
      res.status(200).json({ user: null });
      return;
    }
    const errorBody = await readBackendJson(authResponse);
    forwardJson(res, authResponse.status, errorBody);
    return;
  }

  const auth = await readBackendJson<AuthProfile>(authResponse);
  const profileResponse = await backendRequest({
    req,
    res,
    path: "/users/me",
  });

  const profile = profileResponse.ok ? await readBackendJson<MyProfile>(profileResponse) : null;

  res.status(200).json({
    user: {
      userAuthId: auth.user_auth_id,
      username: auth.username,
      email: auth.email,
      createdAt: auth.created_at,
      displayName: profile?.display_name || auth.username,
      avatarUrl: profile?.avatar_url || null,
      profileLoaded: profileResponse.ok,
    },
  });
}
