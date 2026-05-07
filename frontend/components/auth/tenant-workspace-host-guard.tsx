"use client";

import { useRouter } from "next/router";
import { useEffect, type ReactNode } from "react";

import { useAuth } from "@/hooks/use-auth";
import {
  getApexOriginForRedirects,
  getConfiguredPublicBaseDomain,
  parseTenantSubdomainFromHost,
} from "@/lib/tenant-routing";

/**
 * When the UI is served on a tenant host (e.g. ``tim.devnest.example.com``), ensure the signed-in user
 * owns that route subdomain; otherwise send them to the apex dashboard.
 */
export function TenantWorkspaceHostGuard({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { user, isAuthenticated, isLoading, isCheckingSession } = useAuth();

  useEffect(() => {
    if (!router.isReady || isLoading || isCheckingSession || !isAuthenticated || !user) {
      return;
    }
    const base = getConfiguredPublicBaseDomain();
    if (!base || typeof window === "undefined") {
      return;
    }
    const label = parseTenantSubdomainFromHost(window.location.hostname, base);
    if (!label) {
      return;
    }
    const mine = (user.routeSubdomainSlug || "").trim().toLowerCase();
    if (mine && mine === label) {
      return;
    }
    const apex = getApexOriginForRedirects();
    if (apex) {
      window.location.replace(`${apex}/dashboard?tenant_host_mismatch=1`);
      return;
    }
    void router.replace("/dashboard?tenant_host_mismatch=1");
  }, [router, isAuthenticated, isLoading, isCheckingSession, user]);

  return <>{children}</>;
}
