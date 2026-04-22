import type { NextApiRequest, NextApiResponse } from "next";
import fetch from "node-fetch";

import { getServerBackendBaseUrl } from "@/lib/env";
import {
  clearAuthCookies,
  getAccessTokenCookieName,
  getRefreshTokenCookieName,
  setAuthCookies,
} from "@/lib/server/auth-cookies";

type BackendOptions = {
  req: NextApiRequest;
  res: NextApiResponse;
  path: string;
  method?: string;
  body?: unknown;
  authenticated?: boolean;
  retryOnUnauthorized?: boolean;
  accessTokenOverride?: string;
  refreshTokenOverride?: string;
};

export async function backendRequest({
  req,
  res,
  path,
  method = "GET",
  body,
  authenticated = true,
  retryOnUnauthorized = true,
  accessTokenOverride,
  refreshTokenOverride,
}: BackendOptions) {
  const accessToken = accessTokenOverride || req.cookies[getAccessTokenCookieName()];
  const refreshToken = refreshTokenOverride || req.cookies[getRefreshTokenCookieName()];

  const response = await sendBackendRequest({
    path,
    method,
    body,
    accessToken: authenticated ? accessToken : undefined,
  });

  if (response.status !== 401 || !authenticated || !retryOnUnauthorized || !refreshToken) {
    return response;
  }

  const refreshedAccessToken = await refreshAccessToken(refreshToken);

  if (!refreshedAccessToken) {
    clearAuthCookies(res);
    return response;
  }

  setAuthCookies(res, {
    accessToken: refreshedAccessToken,
    refreshToken,
  });

  return sendBackendRequest({
    path,
    method,
    body,
    accessToken: refreshedAccessToken,
  });
}

async function sendBackendRequest({
  path,
  method,
  body,
  accessToken,
}: {
  path: string;
  method: string;
  body?: unknown;
  accessToken?: string;
}) {
  const headers: Record<string, string> = {
    Accept: "application/json",
  };

  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  return fetch(`${getServerBackendBaseUrl()}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

async function refreshAccessToken(refreshToken: string) {
  const response = await fetch(`${getServerBackendBaseUrl()}/auth/refresh_token`, {
    method: "GET",
    headers: {
      Accept: "application/json",
      "X-Refresh-Token": refreshToken,
    },
  });

  if (!response.ok) {
    return null;
  }

  const data = (await response.json()) as { access_token: string };
  return data.access_token;
}

export async function readBackendJson<T>(response: Awaited<ReturnType<typeof backendRequest>>) {
  const text = await response.text();
  return text ? (JSON.parse(text) as T) : (null as T);
}
