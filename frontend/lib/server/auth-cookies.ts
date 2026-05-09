import type { NextApiResponse } from "next";
import { serialize } from "cookie";

const ACCESS_TOKEN_COOKIE = "devnest_access_token";
const REFRESH_TOKEN_COOKIE = "devnest_refresh_token";
const ACCESS_TOKEN_MAX_AGE_SECONDS = 60 * 30;
const REFRESH_TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24 * 14;

/**
 * When set (e.g. ``.devnest.example.com``), session cookies are visible on tenant subdomains.
 * Falls back to host-only cookies when unset (typical for localhost).
 */
export function getAuthCookieDomain(): string | undefined {
  const raw = (process.env.AUTH_COOKIE_DOMAIN || process.env.NEXT_PUBLIC_DEVNEST_COOKIE_DOMAIN || "").trim();
  if (!raw) {
    return undefined;
  }
  if (raw.toLowerCase() === "localhost" || raw.startsWith("127.")) {
    return undefined;
  }
  return raw.startsWith(".") ? raw : `.${raw}`;
}

/**
 * Browsers ignore Secure cookies on plain HTTP. Production Next on EC2 (http://host:3000)
 * must not set Secure unless the site is HTTPS. Opt in with AUTH_COOKIE_SECURE=true.
 */
function cookieSecureFlag() {
  return process.env.AUTH_COOKIE_SECURE === "true";
}

function buildCookie(name: string, value: string, maxAge: number) {
  const domain = getAuthCookieDomain();
  return serialize(name, value, {
    httpOnly: true,
    sameSite: "lax",
    secure: cookieSecureFlag(),
    path: "/",
    maxAge,
    ...(domain ? { domain } : {}),
  });
}

export function setAuthCookies(
  res: NextApiResponse,
  tokens: {
    accessToken: string;
    refreshToken?: string;
  },
) {
  const cookies = [buildCookie(ACCESS_TOKEN_COOKIE, tokens.accessToken, ACCESS_TOKEN_MAX_AGE_SECONDS)];

  if (tokens.refreshToken) {
    cookies.push(buildCookie(REFRESH_TOKEN_COOKIE, tokens.refreshToken, REFRESH_TOKEN_MAX_AGE_SECONDS));
  }

  res.setHeader("Set-Cookie", cookies);
}

export function clearAuthCookies(res: NextApiResponse) {
  const domain = getAuthCookieDomain();
  const baseOpts = {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: cookieSecureFlag(),
    path: "/",
    maxAge: 0,
    ...(domain ? { domain } : {}),
  };
  res.setHeader("Set-Cookie", [
    serialize(ACCESS_TOKEN_COOKIE, "", baseOpts),
    serialize(REFRESH_TOKEN_COOKIE, "", baseOpts),
  ]);
}

export function getAccessTokenCookieName() {
  return ACCESS_TOKEN_COOKIE;
}

export function getRefreshTokenCookieName() {
  return REFRESH_TOKEN_COOKIE;
}
