import type { NextApiResponse } from "next";
import { serialize } from "cookie";

const ACCESS_TOKEN_COOKIE = "devnest_access_token";
const REFRESH_TOKEN_COOKIE = "devnest_refresh_token";
const ACCESS_TOKEN_MAX_AGE_SECONDS = 60 * 30;
const REFRESH_TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24 * 14;

function buildCookie(name: string, value: string, maxAge: number) {
  return serialize(name, value, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge,
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
  res.setHeader("Set-Cookie", [
    buildCookie(ACCESS_TOKEN_COOKIE, "", 0),
    buildCookie(REFRESH_TOKEN_COOKIE, "", 0),
  ]);
}

export function getAccessTokenCookieName() {
  return ACCESS_TOKEN_COOKIE;
}

export function getRefreshTokenCookieName() {
  return REFRESH_TOKEN_COOKIE;
}
