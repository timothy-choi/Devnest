import type { NextApiRequest, NextApiResponse } from "next";

import { readBackendJson } from "@/lib/server/backend-client";
import { backendRequest } from "@/lib/server/backend-client";
import { setAuthCookies } from "@/lib/server/auth-cookies";
import { forwardJson, sendMethodNotAllowed } from "@/lib/server/http";

type BackendLoginResponse = {
  access_token: string;
  refresh_token: string;
};

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
  if (req.method !== "POST") {
    sendMethodNotAllowed(res, ["POST"]);
    return;
  }

  const loginResponse = await backendRequest({
    req,
    res,
    path: "/auth/login",
    method: "POST",
    body: req.body,
    authenticated: false,
    retryOnUnauthorized: false,
  });

  const loginData = await readBackendJson<BackendLoginResponse | { detail: string }>(loginResponse);

  if (!loginResponse.ok) {
    forwardJson(res, loginResponse.status, loginData);
    return;
  }

  const tokens = loginData as BackendLoginResponse;

  setAuthCookies(res, {
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token,
  });

  const authResponse = await backendRequest({
    req,
    res,
    path: "/auth",
    accessTokenOverride: tokens.access_token,
    refreshTokenOverride: tokens.refresh_token,
  });
  const profileResponse = await backendRequest({
    req,
    res,
    path: "/users/me",
    accessTokenOverride: tokens.access_token,
    refreshTokenOverride: tokens.refresh_token,
  });

  const auth = await readBackendJson<AuthProfile>(authResponse);
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
