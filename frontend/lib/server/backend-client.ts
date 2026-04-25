import type { NextApiRequest, NextApiResponse } from "next";

import { getServerBackendBaseUrl, getServerBackendResolution } from "@/lib/server/internal-api-base";
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
  /** Optional Accept header override (e.g. wildcard) for non-JSON responses. */
  accept?: string;
};

let _serverBackendResolutionLogged = false;

function logServerBackendResolutionOnce(): void {
  if (_serverBackendResolutionLogged) {
    return;
  }
  _serverBackendResolutionLogged = true;
  const r = getServerBackendResolution();
  const fetchMode = r.inDocker ? "docker_network" : "host_next_dev";
  console.info(
    `[DevNest] Next server→FastAPI fetch_mode=${fetchMode} baseUrl=${r.baseUrl} url_source=${r.source} inDocker=${String(r.inDocker)}`,
  );
}

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
  accept,
}: BackendOptions) {
  logServerBackendResolutionOnce();

  const accessToken = accessTokenOverride || req.cookies[getAccessTokenCookieName()];
  const refreshToken = refreshTokenOverride || req.cookies[getRefreshTokenCookieName()];

  const response = await sendBackendRequest({
    path,
    method,
    body,
    accessToken: authenticated ? accessToken : undefined,
    accept,
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
    accept,
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

/**
 * Human-readable classification for API-route JSON / redirects (no secrets).
 */
export function describeBackendFetchFailure(err: unknown): string {
  const text = collectNetworkErrorText(err);
  if (/EAI_AGAIN|ENOTFOUND|getaddrinfo/i.test(text)) {
    return `DNS / name resolution failed — ${text.slice(0, 180)}`;
  }
  if (/ECONNREFUSED|ConnectionRefused/i.test(text)) {
    return `Connection refused (host up but nothing listening on that port) — ${text.slice(0, 180)}`;
  }
  if (/ETIMEDOUT|ConnectTimeout|UND_ERR_CONNECT_TIMEOUT|timeout/i.test(text)) {
    return `Connection timed out — ${text.slice(0, 180)}`;
  }
  if (/ECONNRESET|socket hang up|UND_ERR_SOCKET/i.test(text)) {
    return `Connection reset / dropped — ${text.slice(0, 180)}`;
  }
  if (/fetch failed/i.test(text)) {
    return `Fetch failed (see cause in server logs) — ${text.slice(0, 220)}`;
  }
  return text.slice(0, 220);
}

function createBackendFetchError(classified: string, baseUrl: string, cause: unknown): Error {
  const err = new Error(`${classified} [baseUrl=${baseUrl}]`);
  (err as Error & { cause?: unknown }).cause = cause;
  return err;
}

/**
 * One string for JSON bodies / redirect query params: classified network error + resolved URL + mode hint.
 */
export function backendReachabilityUserDetail(err: unknown): string {
  const { baseUrl, inDocker } = getServerBackendResolution();
  const classified = describeBackendFetchFailure(err);
  const hint = inDocker
    ? "In Compose, INTERNAL_API_BASE_URL must match the API service (default http://backend:8000); confirm the backend container is healthy."
    : "On the host with next dev, NEXT_PUBLIC_API_BASE_URL must point at a running FastAPI (e.g. http://127.0.0.1:8000). docker-compose.dev.yml only runs Postgres — it does not start the API or a backend hostname.";
  return `${classified} Resolved baseUrl=${baseUrl}. ${hint}`;
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
        const r = getServerBackendResolution();
        const fetchMode = r.inDocker ? "docker_network" : "host_next_dev";
        console.warn(
          `[DevNest] Backend fetch failed (attempt ${attempt + 1}/${maxAttempts}) fetch_mode=${fetchMode} baseUrl=${r.baseUrl} url_source=${r.source}`,
        );
        const classified = describeBackendFetchFailure(e);
        throw createBackendFetchError(classified, r.baseUrl, e);
      }
      await new Promise((resolve) => setTimeout(resolve, 400 * (attempt + 1)));
    }
  }
  throw last;
}

async function sendBackendRequest({
  path,
  method,
  body,
  accessToken,
  accept,
}: {
  path: string;
  method: string;
  body?: unknown;
  accessToken?: string;
  accept?: string;
}) {
  const headers: Record<string, string> = {
    Accept: accept ?? "application/json",
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
