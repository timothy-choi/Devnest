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

  let loginResponse: Awaited<ReturnType<typeof backendRequest>>;
  let loginData: BackendLoginResponse | { detail: string } | null;
  try {
    loginResponse = await backendRequest({
      req,
      res,
      path: "/auth/login",
      method: "POST",
      body: req.body,
      authenticated: false,
      retryOnUnauthorized: false,
    });
    loginData = await readBackendJson<BackendLoginResponse | { detail: string }>(loginResponse);
  } catch (err) {
    const message = err instanceof Error ? err.message : "unknown error";
    res.status(503).json({
      detail: `Could not reach the API (${message}). In Docker Compose, set INTERNAL_API_BASE_URL (e.g. http://backend:8000) on the frontend service.`,
    });
    return;
  }

  if (!loginResponse.ok) {
    forwardJson(res, loginResponse.status, loginData);
    return;
  }

  const tokens = loginData as BackendLoginResponse;

  let authResponse: Awaited<ReturnType<typeof backendRequest>>;
  let profileResponse: Awaited<ReturnType<typeof backendRequest>>;
  let auth: AuthProfile;
  let profile: MyProfile | null;
  try {
    authResponse = await backendRequest({
      req,
      res,
      path: "/auth",
      accessTokenOverride: tokens.access_token,
      refreshTokenOverride: tokens.refresh_token,
    });
    profileResponse = await backendRequest({
      req,
      res,
      path: "/users/me",
      accessTokenOverride: tokens.access_token,
      refreshTokenOverride: tokens.refresh_token,
    });

    auth = await readBackendJson<AuthProfile>(authResponse);
    profile = profileResponse.ok ? await readBackendJson<MyProfile>(profileResponse) : null;
  } catch (err) {
    const message = err instanceof Error ? err.message : "unknown error";
    res.status(503).json({
      detail: `Login succeeded but profile could not be loaded (${message}). Check INTERNAL_API_BASE_URL / backend health.`,
    });
    return;
  }

  setAuthCookies(res, {
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token,
  });

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
