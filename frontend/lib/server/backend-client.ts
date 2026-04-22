import type { NextApiRequest, NextApiResponse } from "next";

import { getServerBackendBaseUrl } from "@/lib/server/internal-api-base";
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

function collectNetworkErrorText(err: unknown): string {
  if (!(err instanceof Error)) {
    return String(err);
  }
  const parts: string[] = [err.message];
  const c = err.cause;
  if (c instanceof Error) {
    parts.push(c.message);
  } else if (c !== undefined && c !== null) {
    parts.push(String(c));
  }
  const agg = err as Error & { errors?: unknown[] };
  if (Array.isArray(agg.errors)) {
    for (const sub of agg.errors) {
      parts.push(sub instanceof Error ? sub.message : String(sub));
    }
  }
  return parts.join(" ");
}

function isTransientNetworkError(err: unknown): boolean {
  const msg = collectNetworkErrorText(err);
  return /EAI_AGAIN|ECONNRESET|ECONNREFUSED|ETIMEDOUT|socket hang up|fetch failed|UND_ERR_CONNECT|ConnectionRefused|ConnectTimeoutError/i.test(
    msg,
  );
}

type ServerFetchFn = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

const serverFetch: ServerFetchFn =
  typeof globalThis.fetch === "function"
    ? (globalThis.fetch.bind(globalThis) as ServerFetchFn)
    : // eslint-disable-next-line @typescript-eslint/no-require-imports -- Node < 18 fallback for local dev
      ((require("node-fetch") as typeof import("node-fetch")).default as unknown as ServerFetchFn);

async function fetchWithRetry(url: string, init: RequestInit): Promise<Response> {
  let last: unknown;
  const maxAttempts = 5;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      return await serverFetch(url, init);
    } catch (e) {
      last = e;
      if (!isTransientNetworkError(e) || attempt === maxAttempts - 1) {
        throw e;
      }
      await new Promise((r) => setTimeout(r, 400 * (attempt + 1)));
    }
  }
  throw last;
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

  return fetchWithRetry(`${getServerBackendBaseUrl()}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

async function refreshAccessToken(refreshToken: string) {
  const response = await fetchWithRetry(`${getServerBackendBaseUrl()}/auth/refresh_token`, {
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
  if (!text) {
    return null as T;
  }
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new Error(`Backend returned non-JSON (HTTP ${response.status}): ${text.slice(0, 200)}`);
  }
}
