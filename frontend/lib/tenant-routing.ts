/**
 * Apex vs tenant hostname helpers for multi-tenant routing (Edge-safe; no Node-only APIs).
 */

export function getConfiguredPublicBaseDomain(): string {
  return (process.env.NEXT_PUBLIC_DEVNEST_PUBLIC_BASE_DOMAIN || "").trim().toLowerCase().replace(/^\.+/, "");
}

/** Canonical apex origin for redirects (login ?next=, tenant → apex). */
export function getApexOriginForRedirects(): string | null {
  const explicit = (process.env.NEXT_PUBLIC_DEVNEST_APEX_URL || "").trim();
  if (explicit) {
    try {
      return new URL(explicit).origin;
    } catch {
      return null;
    }
  }
  const base = getConfiguredPublicBaseDomain();
  if (!base) {
    return null;
  }
  const scheme = (process.env.NEXT_PUBLIC_DEVNEST_PUBLIC_SCHEME || "https").replace(/:+$/, "");
  try {
    return new URL(`${scheme}://${base}`).origin;
  } catch {
    return null;
  }
}

/** Dashboard and marketing URLs should use apex when tenant host routing is configured. */
export function getDashboardOriginForAppShell(): string | null {
  return getApexOriginForRedirects();
}

export function parseTenantSubdomainFromHost(hostname: string, baseDomain: string): string | null {
  const h = (hostname || "").split(":")[0].toLowerCase();
  const b = (baseDomain || "").trim().toLowerCase().replace(/^\.+/, "");
  if (!h || !b) {
    return null;
  }
  if (h === b || h === `www.${b}`) {
    return null;
  }
  const suf = `.${b}`;
  if (!h.endsWith(suf)) {
    return null;
  }
  const label = h.slice(0, -suf.length);
  if (!label || label.includes(".")) {
    return null;
  }
  return label;
}

export function isApexHostname(hostname: string, baseDomain: string): boolean {
  const h = hostname.split(":")[0].toLowerCase();
  const b = baseDomain.trim().toLowerCase().replace(/^\.+/, "");
  return h === b || h === `www.${b}`;
}

/**
 * Normalize ``next`` for OAuth/login flows: full URL on our tenant host under /workspaces/*. Does not check slug ownership.
 */
export function normalizeTenantWorkspaceNextUrl(raw: string | undefined | null, baseDomain: string): string | null {
  if (!raw || typeof raw !== "string") {
    return null;
  }
  let decoded = raw.trim();
  try {
    decoded = decodeURIComponent(decoded);
  } catch {
    return null;
  }
  if (decoded.length > 2048) {
    return null;
  }
  const b = baseDomain.trim().toLowerCase().replace(/^\.+/, "");
  if (!b) {
    return null;
  }
  try {
    const u = new URL(decoded);
    const host = u.hostname.toLowerCase();
    if (!host.endsWith(b)) {
      return null;
    }
    if (!u.pathname.startsWith("/workspaces/")) {
      return null;
    }
    const rest = u.pathname.slice("/workspaces/".length).split("/")[0];
    if (!rest) {
      return null;
    }
    return u.toString();
  } catch {
    return null;
  }
}

/**
 * After login: only redirect if tenant subdomain in URL matches the authenticated user's route slug.
 */
export function safePostLoginTenantRedirect(
  nextParam: string | string[] | undefined | null,
  userRouteSubdomainSlug: string | null | undefined,
  baseDomain: string,
): string | null {
  const raw = Array.isArray(nextParam) ? nextParam[0] : nextParam;
  const normalized = normalizeTenantWorkspaceNextUrl(raw ?? null, baseDomain);
  if (!normalized) {
    return null;
  }
  const slug = (userRouteSubdomainSlug || "").trim().toLowerCase();
  if (!slug) {
    return null;
  }
  try {
    const u = new URL(normalized);
    const label = parseTenantSubdomainFromHost(u.hostname, baseDomain);
    if (!label || label !== slug) {
      return null;
    }
    return normalized;
  } catch {
    return null;
  }
}
