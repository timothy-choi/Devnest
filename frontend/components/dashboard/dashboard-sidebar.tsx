"use client";

import { Clock3, FolderOpenDot, Plus, Settings } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

const navItems = [
  { label: "All Workspaces", icon: FolderOpenDot, active: true },
  { label: "Recent", icon: Clock3 },
  { label: "Settings", icon: Settings },
];

export function DashboardSidebar({ onCreateWorkspace }: { onCreateWorkspace: () => void }) {
  return (
    <aside className="hidden w-72 shrink-0 lg:block">
      <Card className="sticky top-24 rounded-[28px] border-white/70 bg-white/85 p-4 shadow-[0_20px_55px_-40px_rgba(15,23,42,0.45)] backdrop-blur">
        <Button className="mb-4 h-12 w-full justify-start rounded-2xl px-4" onClick={onCreateWorkspace}>
          <Plus className="h-4 w-4" />
          New Workspace
        </Button>

        <div className="space-y-1">
          {navItems.map((item) => (
            <button
              key={item.label}
              type="button"
              className={`flex w-full items-center gap-3 rounded-2xl px-4 py-3 text-left text-sm transition ${
                item.active ? "bg-slate-950 text-white shadow-sm" : "text-slate-600 hover:bg-slate-100"
              }`}
            >
              <item.icon className="h-4 w-4" />
              {item.label}
            </button>
          ))}
        </div>
      </Card>
    </aside>
  );
}
