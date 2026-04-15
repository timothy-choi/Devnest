"use client";

import { Plus, Search } from "lucide-react";

import { CreateWorkspaceDialog } from "@/components/dashboard/create-workspace-dialog";
import { DashboardSidebar } from "@/components/dashboard/dashboard-sidebar";
import { DashboardTopNav } from "@/components/dashboard/dashboard-top-nav";
import { WorkspaceGrid } from "@/components/dashboard/workspace-grid";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useWorkspaces } from "@/hooks/use-workspaces";

export function DashboardShell() {
  const workspaceState = useWorkspaces();

  return (
    <main className="min-h-screen bg-[linear-gradient(180deg,_#f8fafc_0%,_#eef2ff_100%)] text-slate-900">
      <DashboardTopNav />
      <div className="mx-auto flex max-w-7xl gap-6 px-4 py-6 sm:px-6 lg:px-8">
        <DashboardSidebar onCreateWorkspace={workspaceState.openCreateDialog} />

        <section className="flex-1 space-y-6">
          <div className="rounded-[28px] border border-white/70 bg-white/80 p-5 shadow-[0_22px_60px_-42px_rgba(15,23,42,0.45)] backdrop-blur sm:p-6">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <p className="text-sm font-medium text-sky-700">Workspace Dashboard</p>
                <h1 className="mt-1 text-3xl font-semibold tracking-tight text-slate-950">All Workspaces</h1>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
                  A mock-driven shell for browsing workspace states, opening projects, and testing lifecycle interactions before backend wiring.
                </p>
              </div>
              <div className="flex flex-col gap-3 sm:flex-row">
                <div className="relative w-full sm:w-72">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                  <Input
                    value={workspaceState.query}
                    onChange={(event) => workspaceState.setQuery(event.target.value)}
                    placeholder="Search workspaces"
                    className="pl-9"
                  />
                </div>
                <Button className="rounded-2xl px-5" onClick={workspaceState.openCreateDialog}>
                  <Plus className="h-4 w-4" />
                  New Workspace
                </Button>
              </div>
            </div>
          </div>

          <WorkspaceGrid
            workspaces={workspaceState.filteredWorkspaces}
            onDelete={workspaceState.deleteWorkspace}
            onRestart={workspaceState.restartWorkspace}
            onStop={workspaceState.stopWorkspace}
            onRunWorkflow={workspaceState.runWorkflow}
            onDownload={workspaceState.downloadWorkspace}
          />
        </section>
      </div>

      <CreateWorkspaceDialog
        open={workspaceState.isCreateDialogOpen}
        onOpenChange={workspaceState.setCreateDialogOpen}
        onCreateWorkspace={workspaceState.createWorkspace}
      />
    </main>
  );
}
