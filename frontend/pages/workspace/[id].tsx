import Head from "next/head";
import { useRouter } from "next/router";
import { Code2, Loader2 } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function WorkspacePage() {
  const router = useRouter();
  const workspaceId = typeof router.query.id === "string" ? router.query.id : "preview";

  return (
    <>
      <Head>
        <title>Workspace {workspaceId} | DevNest</title>
      </Head>
      <main className="min-h-screen bg-[linear-gradient(180deg,_#f8fafc_0%,_#eef2ff_100%)] px-6 py-10">
        <div className="mx-auto flex max-w-5xl flex-col gap-6">
          <div className="space-y-2">
            <p className="text-sm font-medium uppercase tracking-[0.25em] text-slate-500">
              Workspace {workspaceId}
            </p>
            <h1 className="text-3xl font-semibold tracking-tight text-slate-950">
              Opening your workspace...
            </h1>
            <p className="max-w-2xl text-slate-600">
              This placeholder route is ready for the future IDE embed. For now, it gives
              the dashboard a realistic handoff point.
            </p>
          </div>

          <Card className="border-slate-200 bg-white/90 shadow-[0_20px_50px_-35px_rgba(15,23,42,0.45)]">
            <CardHeader className="border-b border-slate-100">
              <CardTitle className="flex items-center gap-3 text-lg">
                <Loader2 className="h-5 w-5 animate-spin text-sky-600" />
                Workspace boot sequence
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-4 p-6 md:grid-cols-[0.9fr_1.1fr]">
              <div className="rounded-3xl border border-dashed border-sky-200 bg-sky-50/70 p-5">
                <p className="text-sm font-medium text-sky-800">Loading shell</p>
                <p className="mt-2 text-sm leading-6 text-sky-700">
                  Environment metadata, session health, and editor status will land here
                  once backend integration begins.
                </p>
              </div>
              <div className="flex min-h-[360px] items-center justify-center rounded-3xl border border-slate-200 bg-slate-950 text-slate-100">
                <div className="space-y-4 text-center">
                  <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-white/10">
                    <Code2 className="h-8 w-8" />
                  </div>
                  <div>
                    <p className="text-lg font-medium">IDE placeholder</p>
                    <p className="mt-1 text-sm text-slate-400">
                      Reserved for the future code-server experience.
                    </p>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </main>
    </>
  );
}
