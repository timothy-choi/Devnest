"use client";

import Link from "next/link";
import { useRouter } from "next/router";
import { Activity, Clock3, FolderOpenDot, Plus, Settings } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

export function DashboardSidebar({ onCreateWorkspace }: { onCreateWorkspace: () => void }) {
  const router = useRouter();
  const path = router.pathname || "";

  return (
    <aside className="hidden w-72 shrink-0 lg:block">
      <Card className="sticky top-24 rounded-[28px] border-white/70 bg-white/85 p-4 shadow-[0_20px_55px_-40px_rgba(15,23,42,0.45)] backdrop-blur">
        <Button className="mb-4 h-12 w-full justify-start rounded-2xl px-4" onClick={onCreateWorkspace}>
          <Plus className="h-4 w-4" />
          New Workspace
        </Button>

        <div className="space-y-1">
          <Link href="/dashboard" passHref>
            <a
              className={`flex w-full items-center gap-3 rounded-2xl px-4 py-3 text-left text-sm transition ${
                path === "/dashboard" ? "bg-slate-950 text-white shadow-sm" : "text-slate-600 hover:bg-slate-100"
              }`}
            >
              <FolderOpenDot className="h-4 w-4" />
              All Workspaces
            </a>
          </Link>
          <Link href="/system-status" passHref>
            <a
              className={`flex w-full items-center gap-3 rounded-2xl px-4 py-3 text-left text-sm transition ${
                path === "/system-status" ? "bg-slate-950 text-white shadow-sm" : "text-slate-600 hover:bg-slate-100"
              }`}
            >
              <Activity className="h-4 w-4" />
              System status
            </a>
          </Link>
          <button
            type="button"
            className="flex w-full items-center gap-3 rounded-2xl px-4 py-3 text-left text-sm text-slate-600 transition hover:bg-slate-100"
          >
            <Clock3 className="h-4 w-4" />
            Recent
          </button>
          <button
            type="button"
            className="flex w-full items-center gap-3 rounded-2xl px-4 py-3 text-left text-sm text-slate-600 transition hover:bg-slate-100"
          >
            <Settings className="h-4 w-4" />
            Settings
          </button>
        </div>
      </Card>
    </aside>
  );
}
