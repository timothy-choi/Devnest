/**
 * Collect ``Set-Cookie`` header lines from a fetch ``Response``.
 * Node 18+ undici exposes ``getSetCookie()``; node-fetch v2 uses ``headers.raw()['set-cookie']``.
 */
export function getSetCookieHeaderValues(headers: Headers): string[] {
  const h = headers as Headers & {
    getSetCookie?: () => string[];
    raw?: () => Record<string, string[] | string | undefined>;
  };
  if (typeof h.getSetCookie === "function") {
    const v = h.getSetCookie();
    return Array.isArray(v) ? v : [];
  }
  if (typeof h.raw === "function") {
    const raw = h.raw();
    const c = raw["set-cookie"] ?? raw["Set-Cookie"];
    if (Array.isArray(c)) {
      return c;
    }
    if (typeof c === "string") {
      return [c];
    }
  }
  const single = headers.get("set-cookie");
  return single ? [single] : [];
}
