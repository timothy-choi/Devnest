import Head from "next/head";
import { useRouter } from "next/router";
import { useEffect, useRef, useState } from "react";
import { Code2, Loader2 } from "lucide-react";

import { useAuth } from "@/hooks/use-auth";
import { AuthGuard } from "@/components/auth/auth-guard";
import { TenantWorkspaceHostGuard } from "@/components/auth/tenant-workspace-host-guard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type WorkspaceDetailJson = {
  workspace_id?: number;
  url_slug?: string;
  status?: string;
  last_error_message?: string | null;
  reopen_issues?: string[];
};

type AttachJson = {
  accepted?: boolean;
  gateway_url?: string | null;
  issues?: string[];
  detail?: string;
};

export default function WorkspaceBySlugPage() {
  const router = useRouter();
  const { isAuthenticated, isLoading, isCheckingSession } = useAuth();
  const slug = typeof router.query.slug === "string" ? router.query.slug : "preview";
  const [message, setMessage] = useState<string | null>(null);
  const opened = useRef(false);

  const redirectToDashboard = () => {
    if (typeof window === "undefined") {
      void router.replace("/dashboard");
      return;
    }
    window.location.replace("/dashboard");
  };

  useEffect(() => {
    opened.current = false;
    setMessage(null);
  }, [slug]);

  useEffect(() => {
    if (!router.isReady || isLoading || isCheckingSession) {
      return;
    }
    if (!isAuthenticated) {
      return;
    }
    if (slug === "preview") {
      return;
    }

    const navEntry = typeof window !== "undefined" ? window.performance.getEntriesByType("navigation")[0] : null;
    const navType =
      navEntry && "type" in navEntry ? String((navEntry as PerformanceNavigationTiming).type || "") : "";
    if (navType === "back_forward") {
      opened.current = true;
      redirectToDashboard();
      return;
    }
    if (opened.current) {
      return;
    }

    let cancelled = false;

    const run = async () => {
      opened.current = true;
      try {
        const detailRes = await fetch(`/api/workspaces/by-url-slug/${encodeURIComponent(slug)}`);
        const detail = (await detailRes.json()) as WorkspaceDetailJson & { detail?: string };
        if (!detailRes.ok) {
          setMessage(typeof detail.detail === "string" ? detail.detail : "Unable to load workspace.");
          redirectToDashboard();
          return;
        }
        const workspaceId = detail.workspace_id;
        if (typeof workspaceId !== "number") {
          setMessage("Workspace response missing id.");
          redirectToDashboard();
          return;
        }
        if ((detail.status || "").toUpperCase() !== "RUNNING") {
          setMessage(
            "This workspace is not running yet. Start it from the dashboard, wait until it is RUNNING, then open again.",
          );
          redirectToDashboard();
          return;
        }
        if (detail.reopen_issues?.length) {
          setMessage(detail.reopen_issues.join("; "));
          redirectToDashboard();
          return;
        }

        const maxAttachAttempts = 8;
        let attach: AttachJson | null = null;
        for (let attempt = 0; attempt < maxAttachAttempts; attempt++) {
          const attachRes = await fetch(`/api/workspaces/${workspaceId}/attach`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
          });
          attach = (await attachRes.json()) as AttachJson;
          if (attachRes.ok) {
            break;
          }
          const errDetail = typeof attach.detail === "string" ? attach.detail : "";
          const transientDetail =
            /retry shortly|not ready|reconcile job was queued|timeout|traefik|gateway edge|ide upstream|restart workspace/i;
          const transient =
            (attachRes.status === 503 || attachRes.status === 409) && transientDetail.test(errDetail);
          if (transient && attempt < maxAttachAttempts - 1) {
            await new Promise((r) => setTimeout(r, 180 + attempt * 140));
            continue;
          }
          setMessage(errDetail || "Unable to attach to this workspace.");
          redirectToDashboard();
          return;
        }
        if (!attach) {
          setMessage("Unable to attach to this workspace.");
          redirectToDashboard();
          return;
        }
        if (!attach.accepted) {
          const fromIssues = attach.issues?.length ? attach.issues.join("; ") : null;
          setMessage(fromIssues || "Attach was not accepted for this workspace.");
          redirectToDashboard();
          return;
        }

        const gatewayUrl = (attach.gateway_url || "").trim();
        if (gatewayUrl) {
          window.location.replace(gatewayUrl);
          return;
        }

        setMessage(
          "No gateway URL was returned. Ensure the API has DEVNEST_GATEWAY_ENABLED, a reachable DEVNEST_GATEWAY_URL " +
            "(route-admin), and DEVNEST_BASE_DOMAIN aligned with Traefik Host rules. If Traefik is published on a " +
            "non-default port, set DEVNEST_GATEWAY_PUBLIC_PORT to match (see docker-compose.integration.yml).",
        );
        redirectToDashboard();
      } catch {
        if (!cancelled) {
          setMessage("Something went wrong while opening the workspace.");
          redirectToDashboard();
        }
      }
    };

    void run();
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, isCheckingSession, isLoading, router, slug]);

  return (
    <AuthGuard>
      <TenantWorkspaceHostGuard>
        <>
          <Head>
            <title>Workspace /{slug} | DevNest</title>
          </Head>
        <main className="min-h-screen bg-[linear-gradient(180deg,_#f8fafc_0%,_#eef2ff_100%)] px-6 py-10">
          <div className="mx-auto flex max-w-5xl flex-col gap-6">
            <div className="space-y-2">
              <p className="text-sm font-medium uppercase tracking-[0.25em] text-slate-500">
                Workspace /workspaces/{slug}
              </p>
              <h1 className="text-3xl font-semibold tracking-tight text-slate-950">Opening your workspace…</h1>
              <p className="max-w-2xl text-slate-600">
                Tenant URLs use your route subdomain and this slug under <code className="text-sm">/workspaces/</code>.
                When the gateway is enabled, you are redirected to code-server on the edge domain.
              </p>
            </div>

            <Card className="border-slate-200 bg-white/90 shadow-[0_20px_50px_-35px_rgba(15,23,42,0.45)]">
              <CardHeader className="border-b border-slate-100">
                <CardTitle className="flex items-center gap-3 text-lg">
                  {message ? (
                    <Code2 className="h-5 w-5 text-amber-600" />
                  ) : (
                    <Loader2 className="h-5 w-5 animate-spin text-sky-600" />
                  )}
                  {message ? "Could not open in the browser" : "Connecting to the IDE gateway"}
                </CardTitle>
              </CardHeader>
              <CardContent className="grid gap-4 p-6 md:grid-cols-[0.9fr_1.1fr]">
                <div className="rounded-3xl border border-dashed border-sky-200 bg-sky-50/70 p-5">
                  <p className="text-sm font-medium text-sky-800">What happens next</p>
                  <p className="mt-2 text-sm leading-6 text-sky-700">
                    The app resolves your workspace by slug, calls attach to mint a session, then navigates to the
                    Traefik route for code-server (tenant path prefix when multi-tenant routing is enabled).
                  </p>
                </div>
                <div className="flex min-h-[360px] items-center justify-center rounded-3xl border border-slate-200 bg-slate-950 px-6 text-center text-slate-100">
                  {message ? (
                    <p className="text-sm leading-7 text-slate-200">{message}</p>
                  ) : (
                    <div className="space-y-4">
                      <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-white/10">
                        <Loader2 className="h-8 w-8 animate-spin" />
                      </div>
                      <p className="text-sm text-slate-400">Preparing redirect to code-server…</p>
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          </div>
        </main>
      </>
      </TenantWorkspaceHostGuard>
    </AuthGuard>
  );
}
