import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

/**
 * Middleware runs as a **Vercel Edge Function** (and Next.js Edge runtime). It must stay self-contained:
 * do not import `@/lib/*` or other app modules — bundlers may pull unsupported code into the edge bundle.
 * Tenant hostname parsing below mirrors `lib/tenant-routing.ts` for the subset needed here only.
 */

function getFrontendWorkspaceDomainMode(): string {
  return (process.env.NEXT_PUBLIC_DEVNEST_WORKSPACE_DOMAIN_MODE || "").trim().toLowerCase();
}

function getConfiguredPublicBaseDomain(): string {
  return (process.env.NEXT_PUBLIC_DEVNEST_PUBLIC_BASE_DOMAIN || "").trim().toLowerCase().replace(/^\.+/, "");
}

function getApexOriginForRedirects(): string | null {
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

function parseTenantSubdomainFromHost(hostname: string, baseDomain: string): string | null {
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

function isApexHostname(hostname: string, baseDomain: string): boolean {
  const h = hostname.split(":")[0].toLowerCase();
  const b = baseDomain.trim().toLowerCase().replace(/^\.+/, "");
  return h === b || h === `www.${b}`;
}

const ACCESS = "devnest_access_token";
const REFRESH = "devnest_refresh_token";

function skipTenantMiddleware(pathname: string): boolean {
  if (pathname.startsWith("/_next")) {
    return true;
  }
  if (pathname === "/favicon.ico") {
    return true;
  }
  if (/\.(?:svg|png|jpg|jpeg|gif|webp|ico)$/i.test(pathname)) {
    return true;
  }
  return false;
}

async function routeTenantExists(subdomain: string): Promise<boolean | null> {
  const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "").trim();
  if (!apiBase) {
    return null;
  }
  let origin: string;
  try {
    origin = new URL(apiBase).origin;
  } catch {
    return null;
  }
  const url = `${origin}/auth/public/route-tenants/${encodeURIComponent(subdomain)}`;
  try {
    const res = await fetch(url, { method: "GET", redirect: "manual", cache: "no-store" });
    if (res.status === 204) {
      return true;
    }
    if (res.status === 404) {
      return false;
    }
    return null;
  } catch {
    return null;
  }
}

export async function middleware(request: NextRequest) {
  const pathname = request.nextUrl.pathname;
  if (skipTenantMiddleware(pathname)) {
    return NextResponse.next();
  }

  // Legacy / default: no tenant host redirects (sslip, Vercel preview, etc.).
  if (getFrontendWorkspaceDomainMode() !== "tenant") {
    return NextResponse.next();
  }

  const baseDomain = getConfiguredPublicBaseDomain();
  if (!baseDomain) {
    return NextResponse.next();
  }

  const hostHeader = request.headers.get("host") || "";
  const hostname = hostHeader.split(":")[0].toLowerCase();
  if (isApexHostname(hostname, baseDomain)) {
    return NextResponse.next();
  }

  if (!hostname.endsWith(`.${baseDomain}`)) {
    return NextResponse.next();
  }

  const subdomain = parseTenantSubdomainFromHost(hostname, baseDomain);
  if (!subdomain) {
    return NextResponse.next();
  }

  const apex = getApexOriginForRedirects();
  if (!apex) {
    return NextResponse.next();
  }

  const exists = await routeTenantExists(subdomain);
  if (exists === false) {
    const dest = new URL("/tenant-not-found", apex);
    dest.searchParams.set("subdomain", subdomain);
    return NextResponse.redirect(dest);
  }

  const marketingOnApex = new Set(["/", "/login", "/signup", "/register", "/pricing", "/docs"]);
  if (marketingOnApex.has(pathname) || pathname.startsWith("/api/auth/oauth")) {
    const dest = new URL(pathname, apex);
    dest.search = request.nextUrl.search;
    return NextResponse.redirect(dest);
  }

  if (pathname.startsWith("/api")) {
    return NextResponse.next();
  }

  if (pathname.startsWith("/workspaces/")) {
    const access = request.cookies.get(ACCESS) as unknown;
    const refresh = request.cookies.get(REFRESH) as unknown;
    const accessTok = typeof access === "string" ? access : (access as { value?: string } | undefined)?.value;
    const refreshTok = typeof refresh === "string" ? refresh : (refresh as { value?: string } | undefined)?.value;
    const hasAuth = Boolean(accessTok || refreshTok);
    if (!hasAuth) {
      const loginUrl = new URL("/login", apex);
      loginUrl.searchParams.set("next", request.nextUrl.toString());
      return NextResponse.redirect(loginUrl);
    }
    return NextResponse.next();
  }

  const dest = new URL(pathname === "/" ? "/" : pathname, apex);
  dest.search = request.nextUrl.search;
  return NextResponse.redirect(dest);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image).*)"],
};
