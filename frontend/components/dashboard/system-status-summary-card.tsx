"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";

import { browserApi } from "@/lib/api/browser-client";

export function SystemStatusSummaryCard() {
  const q = useQuery({
    queryKey: ["systemStatus"],
    queryFn: () => browserApi.system.status(),
    staleTime: 30_000,
  });

  if (q.isLoading || q.isError || !q.data) {
    return null;
  }

  const d = q.data;
  const db = d.databaseConnected ? "DB connected" : "DB unreachable";
  const snap = `${d.snapshotStorage.provider} snapshots`;
  const gw = d.gateway.enabled ? `Gateway ${d.gateway.baseDomain}` : "Gateway off";

  return (
    <Link href="/system-status" passHref>
      <a className="block rounded-[20px] border border-sky-100 bg-gradient-to-r from-sky-50/90 to-white/80 px-4 py-3 text-sm text-slate-700 shadow-sm transition hover:border-sky-200 hover:shadow-md">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="font-medium text-slate-900">System status</p>
            <p className="mt-0.5 text-xs text-slate-600">
              {db} · {snap} · {gw}
            </p>
          </div>
          <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" aria-hidden />
        </div>
      </a>
    </Link>
  );
}
