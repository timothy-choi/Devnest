import Head from "next/head";
import { useRouter } from "next/router";
import { useEffect, useRef, useState } from "react";
import { Code2, Loader2 } from "lucide-react";

import { useAuth } from "@/hooks/use-auth";
import { AuthGuard } from "@/components/auth/auth-guard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type WorkspaceDetailJson = {
  status?: string;
  last_error_message?: string | null;
};

type AttachJson = {
  accepted?: boolean;
  gateway_url?: string | null;
  issues?: string[];
  detail?: string;
};

export default function WorkspacePage() {
  const router = useRouter();
  const { isAuthenticated, isLoading, isCheckingSession } = useAuth();
  const workspaceId = typeof router.query.id === "string" ? router.query.id : "preview";
  const [message, setMessage] = useState<string | null>(null);
  const opened = useRef(false);

  const redirectToDashboard = () => {
    void router.replace("/dashboard");
  };

  useEffect(() => {
    if (!router.isReady || isLoading || isCheckingSession) {
      return;
    }
    if (!isAuthenticated) {
      return;
    }
    if (workspaceId === "preview" || opened.current) {
      return;
    }

    const navEntry = typeof window !== "undefined" ? window.performance.getEntriesByType("navigation")[0] : null;
    const navType =
      navEntry && "type" in navEntry
        ? String((navEntry as PerformanceNavigationTiming).type || "")
        : "";
    if (navType === "back_forward") {
      opened.current = true;
      redirectToDashboard();
      return;
    }

    let cancelled = false;

    const run = async () => {
      opened.current = true;
      try {
        const detailRes = await fetch(`/api/workspaces/${workspaceId}`);
        const detail = (await detailRes.json()) as WorkspaceDetailJson & { detail?: string };
        if (!detailRes.ok) {
          setMessage(typeof detail.detail === "string" ? detail.detail : "Unable to load workspace.");
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

        const attachRes = await fetch(`/api/workspaces/${workspaceId}/attach`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        const attach = (await attachRes.json()) as AttachJson;
        if (!attachRes.ok) {
          setMessage(typeof attach.detail === "string" ? attach.detail : "Unable to attach to this workspace.");
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
  }, [isAuthenticated, isCheckingSession, isLoading, router, workspaceId]);

  return (
    <AuthGuard>
      <>
        <Head>
          <title>Workspace {workspaceId} | DevNest</title>
        </Head>
        <main className="min-h-screen bg-[linear-gradient(180deg,_#f8fafc_0%,_#eef2ff_100%)] px-6 py-10">
          <div className="mx-auto flex max-w-5xl flex-col gap-6">
            <div className="space-y-2">
              <p className="text-sm font-medium uppercase tracking-[0.25em] text-slate-500">Workspace {workspaceId}</p>
              <h1 className="text-3xl font-semibold tracking-tight text-slate-950">Opening your workspace…</h1>
              <p className="max-w-2xl text-slate-600">
                When the gateway is enabled, you are redirected to the code-server host on your edge domain. The
                control plane stores an HttpOnly session cookie for Traefik ForwardAuth when gateway auth is on.
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
                    The app loads workspace metadata, calls attach to mint a session, then navigates to the Traefik
                    route for code-server (same host cookie when your API and gateway share a registrable domain).
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
    </AuthGuard>
  );
}
