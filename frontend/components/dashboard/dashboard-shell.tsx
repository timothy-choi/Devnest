"use client";

import { Plus, Search } from "lucide-react";
import { useEffect, useState } from "react";

import { CreateWorkspaceDialog } from "@/components/dashboard/create-workspace-dialog";
import { DashboardSidebar } from "@/components/dashboard/dashboard-sidebar";
import { NotificationCenterDialog } from "@/components/dashboard/notification-center-dialog";
import { NotificationToastStack } from "@/components/dashboard/notification-toast-stack";
import { DashboardTopNav } from "@/components/dashboard/dashboard-top-nav";
import { WorkspaceGrid } from "@/components/dashboard/workspace-grid";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useNotifications } from "@/hooks/use-notifications";
import { useWorkspaces } from "@/hooks/use-workspaces";

export function DashboardShell() {
  const workspaceState = useWorkspaces();
  const notificationState = useNotifications();
  const [isNotificationCenterOpen, setNotificationCenterOpen] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    window.sessionStorage.removeItem("devnestWorkspaceReturnTarget");

    const currentUrl = new URL(window.location.href);
    if (currentUrl.pathname !== "/dashboard" || currentUrl.searchParams.get("workspaceReturn") !== "1") {
      return;
    }

    window.history.replaceState(window.history.state, "", "/dashboard");
  }, []);

  return (
    <main className="min-h-screen bg-[linear-gradient(180deg,_#f8fafc_0%,_#eef2ff_100%)] text-slate-900">
      <DashboardTopNav
        unreadCount={notificationState.unreadCount}
        onOpenNotifications={() => setNotificationCenterOpen(true)}
      />
      <div className="mx-auto flex max-w-7xl gap-6 px-4 py-6 sm:px-6 lg:px-8">
        <DashboardSidebar onCreateWorkspace={workspaceState.openCreateDialog} />

        <section className="flex-1 space-y-6">
          <div className="rounded-[28px] border border-white/70 bg-white/80 p-5 shadow-[0_22px_60px_-42px_rgba(15,23,42,0.45)] backdrop-blur sm:p-6">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <p className="text-sm font-medium text-sky-700">Workspace Dashboard</p>
                <h1 className="mt-1 text-3xl font-semibold tracking-tight text-slate-950">All Workspaces</h1>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
                  Browse your real DevNest workspaces, manage lifecycle requests, and verify backend connectivity from the existing dashboard shell.
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

          {workspaceState.actionError ? (
            <div className="rounded-[24px] border border-amber-200 bg-amber-50 px-5 py-4 text-sm text-amber-800 shadow-[0_18px_45px_-40px_rgba(15,23,42,0.45)]">
              {workspaceState.actionError}
            </div>
          ) : null}

          {workspaceState.hasBusyWorkspace ? (
            <div className="rounded-[24px] border border-sky-200 bg-sky-50/80 px-5 py-4 text-sm text-sky-900 shadow-[0_18px_45px_-40px_rgba(15,23,42,0.45)]">
              Transitional workspace states are controlled by backend jobs. If a workspace stays in
              <span className="font-medium"> CREATING</span> or another busy state, make sure the backend worker is running locally.
            </div>
          ) : null}

          <div className="flex flex-wrap gap-2 rounded-[20px] border border-slate-200/80 bg-white/70 p-2 shadow-sm">
            {(
              [
                { id: "active" as const, label: "Workspaces" },
                {
                  id: "restore_required" as const,
                  label: `Restore required${workspaceState.restoreRequiredList.length ? ` (${workspaceState.restoreRequiredList.length})` : ""}`,
                },
                {
                  id: "unrecoverable" as const,
                  label: `Data lost${workspaceState.unrecoverableList.length ? ` (${workspaceState.unrecoverableList.length})` : ""}`,
                },
              ] as const
            ).map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => workspaceState.setDashboardSection(tab.id)}
                className={`rounded-xl px-4 py-2 text-sm font-medium transition ${
                  workspaceState.dashboardSection === tab.id
                    ? "bg-slate-900 text-white shadow"
                    : "text-slate-600 hover:bg-slate-100"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <WorkspaceGrid
            workspaces={workspaceState.filteredWorkspaces}
            isLoading={workspaceState.isLoading}
            errorMessage={workspaceState.errorMessage}
            emptyTitle={
              workspaceState.dashboardSection === "restore_required"
                ? "Nothing needs a snapshot restore"
                : workspaceState.dashboardSection === "unrecoverable"
                  ? "No unrecoverable workspaces"
                  : "No workspaces found"
            }
            emptyDescription={
              workspaceState.dashboardSection === "restore_required"
                ? "Workspaces missing on disk but with an AVAILABLE snapshot appear here."
                : workspaceState.dashboardSection === "unrecoverable"
                  ? "Workspaces whose project data is gone and have no snapshot appear here so you can remove them without cluttering the main list."
                  : "Try a different search or create a fresh workspace to populate the grid."
            }
            onOpen={workspaceState.openWorkspace}
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
        isSubmitting={workspaceState.isCreating}
        submitError={workspaceState.createError}
      />

      <NotificationCenterDialog
        open={isNotificationCenterOpen}
        onOpenChange={setNotificationCenterOpen}
        notifications={notificationState.notifications}
        unreadCount={notificationState.unreadCount}
        enabledTypes={notificationState.enabledTypes}
        channelSettings={notificationState.channelSettings}
        isLoading={notificationState.isLoading}
        isSaving={notificationState.isSavingPreferences}
        isMarkingAllRead={notificationState.isMarkingAllRead}
        errorMessage={notificationState.errorMessage}
        onSavePreferences={notificationState.savePreferences}
        onMarkAllRead={notificationState.markAllRead}
      />

      <NotificationToastStack
        notifications={notificationState.notifications}
        enabledTypes={notificationState.enabledTypes}
        inAppEnabled={notificationState.channelSettings.inAppEnabled}
        pushEnabled={notificationState.channelSettings.pushEnabled}
        unreadCount={notificationState.unreadCount}
      />
    </main>
  );
}
