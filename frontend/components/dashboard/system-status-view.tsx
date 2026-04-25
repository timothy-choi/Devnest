"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowLeft, Database, HardDrive, Server, Wifi } from "lucide-react";

import { browserApi } from "@/lib/api/browser-client";
import { Card } from "@/components/ui/card";

function StatusRow({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="flex flex-col gap-0.5 border-b border-slate-100 py-3 last:border-0 sm:flex-row sm:items-center sm:justify-between">
      <span className="text-sm text-slate-500">{label}</span>
      <span
        className={`font-mono text-sm font-medium ${ok === false ? "text-amber-700" : ok === true ? "text-emerald-700" : "text-slate-900"}`}
      >
        {value}
      </span>
    </div>
  );
}

export function SystemStatusView() {
  const q = useQuery({
    queryKey: ["systemStatus"],
    queryFn: () => browserApi.system.status(),
  });

  const data = q.data;

  return (
    <main className="min-h-screen bg-[linear-gradient(180deg,_#f8fafc_0%,_#eef2ff_100%)] text-slate-900">
      <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
        <div className="mb-6 flex items-center gap-3">
          <Link href="/dashboard" passHref>
            <a className="inline-flex items-center gap-1 rounded-xl px-3 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-100">
              <ArrowLeft className="h-4 w-4" />
              Dashboard
            </a>
          </Link>
        </div>

        <div className="mb-6">
          <p className="text-sm font-medium text-sky-700">Operations</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-slate-950">System status</h1>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Live view of control-plane health and configuration (no secrets). Data comes from{" "}
            <code className="rounded bg-slate-100 px-1 py-0.5 text-xs">GET /system/status</code>.
          </p>
        </div>

        {q.isLoading ? (
          <Card className="rounded-[24px] border-white/70 bg-white/90 p-6 shadow-sm backdrop-blur">
            <p className="text-sm text-slate-600">Loading status…</p>
          </Card>
        ) : q.isError ? (
          <Card className="rounded-[24px] border border-amber-200 bg-amber-50 p-6 text-sm text-amber-900">
            Could not load status. Ensure you are signed in and the API is reachable.
          </Card>
        ) : data ? (
          <div className="space-y-4">
            <Card className="rounded-[24px] border-white/70 bg-white/90 p-5 shadow-sm backdrop-blur sm:p-6">
              <div className="mb-4 flex items-center gap-2 text-slate-800">
                <Activity className="h-5 w-5 text-sky-600" />
                <h2 className="text-lg font-semibold">Backend</h2>
              </div>
              <StatusRow label="API process" value={data.backendOk ? "ok" : "degraded"} ok={data.backendOk} />
              <StatusRow label="Database" value={data.databaseConnected ? "connected" : "unreachable"} ok={data.databaseConnected} />
              <StatusRow label="DB host" value={data.databaseHost || "—"} />
              <StatusRow label="DB name" value={data.databaseName || "—"} />
            </Card>

            <Card className="rounded-[24px] border-white/70 bg-white/90 p-5 shadow-sm backdrop-blur sm:p-6">
              <div className="mb-4 flex items-center gap-2 text-slate-800">
                <HardDrive className="h-5 w-5 text-sky-600" />
                <h2 className="text-lg font-semibold">Snapshot storage</h2>
              </div>
              <StatusRow label="Provider" value={data.snapshotStorage.provider} />
              {data.snapshotStorage.provider === "s3" ? (
                <>
                  <StatusRow label="Bucket" value={data.snapshotStorage.bucket || "—"} />
                  <StatusRow label="Prefix" value={data.snapshotStorage.prefix || "—"} />
                  <StatusRow label="Region" value={data.snapshotStorage.region || "—"} />
                </>
              ) : (
                <StatusRow label="Local root" value={data.snapshotStorage.root || "—"} />
              )}
            </Card>

            <Card className="rounded-[24px] border-white/70 bg-white/90 p-5 shadow-sm backdrop-blur sm:p-6">
              <div className="mb-4 flex items-center gap-2 text-slate-800">
                <Wifi className="h-5 w-5 text-sky-600" />
                <h2 className="text-lg font-semibold">Gateway</h2>
              </div>
              <StatusRow label="Enabled" value={data.gateway.enabled ? "yes" : "no"} ok={data.gateway.enabled} />
              <StatusRow label="Base domain" value={data.gateway.baseDomain || "—"} />
              <StatusRow
                label="Public URL"
                value={`${data.gateway.publicScheme}://ws-<id>.${data.gateway.baseDomain || "?"}${
                  data.gateway.publicPort && data.gateway.publicPort !== 80 && data.gateway.publicPort !== 443
                    ? `:${data.gateway.publicPort}`
                    : ""
                }`}
              />
              <StatusRow label="ForwardAuth" value={data.gateway.authEnabled ? "on" : "off"} />
              <StatusRow label="Route admin host" value={data.gateway.routeAdminHost || "—"} />
            </Card>

            <Card className="rounded-[24px] border-white/70 bg-white/90 p-5 shadow-sm backdrop-blur sm:p-6">
              <div className="mb-4 flex items-center gap-2 text-slate-800">
                <Server className="h-5 w-5 text-sky-600" />
                <h2 className="text-lg font-semibold">Job worker</h2>
              </div>
              <StatusRow label="Deployment" value={data.worker.deploymentModel} />
              <StatusRow
                label="In-process worker"
                value={
                  data.worker.deploymentModel === "in_process"
                    ? data.worker.inProcessTaskRunning
                      ? "running"
                      : "enabled (not running)"
                    : "disabled (use workspace-worker service)"
                }
                ok={
                  data.worker.deploymentModel === "standalone"
                    ? undefined
                    : Boolean(data.worker.inProcessEnabled && data.worker.inProcessTaskRunning)
                }
              />
              <StatusRow label="Jobs queued" value={String(data.worker.jobsQueued)} />
              <StatusRow label="Jobs running" value={String(data.worker.jobsRunning)} />
            </Card>

            <Card className="rounded-[24px] border-white/70 bg-white/90 p-5 shadow-sm backdrop-blur sm:p-6">
              <div className="mb-4 flex items-center gap-2 text-slate-800">
                <Database className="h-5 w-5 text-sky-600" />
                <h2 className="text-lg font-semibold">Build</h2>
              </div>
              <StatusRow label="Environment" value={data.application.devnestEnv} />
              <StatusRow label="Version" value={data.application.version || "—"} />
              <StatusRow label="Git commit" value={data.application.gitCommit || "—"} />
              <StatusRow label="Generated" value={data.generatedAt ? new Date(data.generatedAt).toLocaleString() : "—"} />
            </Card>
          </div>
        ) : null}
      </div>
    </main>
  );
}
