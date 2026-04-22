const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";
const DEFAULT_APP_BASE_URL = "http://localhost:3000";

function isLoopbackHostname(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
}

export function getApiBaseUrl() {
  const baked = process.env.NEXT_PUBLIC_API_BASE_URL || DEFAULT_API_BASE_URL;
  if (typeof window !== "undefined") {
    try {
      const bakedUrl = new URL(baked);
      const currentUrl = new URL(window.location.origin);
      if (isLoopbackHostname(bakedUrl.hostname) && !isLoopbackHostname(currentUrl.hostname)) {
        const next = new URL(window.location.origin);
        const port = next.port || (next.protocol === "https:" ? "443" : "80");
        if (port === "3000") {
          next.port = "8000";
        } else if (port === "80") {
          next.port = "8000";
        } else if (port === "443") {
          next.port = "8000";
        } else {
          return baked;
        }
        return next.toString().replace(/\/$/, "");
      }
    } catch {
      return baked;
    }
  }
  return baked;
}

/**
 * Public UI base URL. ``NEXT_PUBLIC_APP_BASE_URL`` is inlined at ``next build``; when that value is
 * still loopback but the user opened EC2/sslip/DNS, use the browser origin so links and OAuth
 * return paths match the tab the user is actually on.
 */
export function getAppBaseUrl() {
  const baked = process.env.NEXT_PUBLIC_APP_BASE_URL || DEFAULT_APP_BASE_URL;
  if (typeof window !== "undefined") {
    try {
      const bakedUrl = new URL(baked);
      const currentUrl = new URL(window.location.origin);
      if (isLoopbackHostname(bakedUrl.hostname) && !isLoopbackHostname(currentUrl.hostname)) {
        return window.location.origin;
      }
    } catch {
      return baked;
    }
  }
  return baked;
}

export function getAppOrigin() {
  try {
    return new URL(getAppBaseUrl()).origin;
  } catch {
    return null;
  }
}
