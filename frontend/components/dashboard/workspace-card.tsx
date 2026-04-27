"use client";

import { useState } from "react";
import {
  ArchiveRestore,
  Download,
  Loader2,
  MoreVertical,
  PlayCircle,
  RotateCcw,
  Save,
  Square,
  Trash2,
} from "lucide-react";

import { DetailedStatusBadge } from "@/components/dashboard/workspace-status-badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Workspace } from "@/types/workspace";

function saveWorkspaceDisabledReason(workspace: Workspace): string | null {
  const isPending = workspace.pendingAction !== null;
  const isRestore = workspace.projectDataLifecycle === "restore_required";
  const isUnrecoverable = workspace.projectDataLifecycle === "unrecoverable";
  const eligible = workspace.rawStatus === "RUNNING" || workspace.rawStatus === "STOPPED";

  if (isPending) {
    return "Finish the current workspace action first.";
  }
  if (isRestore) {
    return "Restore project files from a snapshot before saving from the dashboard.";
  }
  if (isUnrecoverable) {
    return "Disk-backed project files are not available for this workspace.";
  }
  if (!eligible) {
    return "Saving is available when the workspace is running or stopped.";
  }
  return null;
}

type WorkspaceCardProps = {
  workspace: Workspace;
  onOpen: (id: string) => void;
  onStop: (id: string) => void;
  onRestart: (id: string) => void;
  onDelete: (id: string) => void;
  onDownload: (id: string) => void;
  onSaveWorkspace: (id: string) => void;
  onRunWorkflow: (id: string) => void;
  snapshotBusyWorkspaceId: number | null;
};

export function WorkspaceCard({
  workspace,
  onOpen,
  onStop,
  onRestart,
  onDelete,
  onDownload,
  onSaveWorkspace,
  onRunWorkflow,
  snapshotBusyWorkspaceId,
}: WorkspaceCardProps) {
  const [recoverOpen, setRecoverOpen] = useState(false);
  const isPending = workspace.pendingAction !== null;
  const isDeleting = workspace.pendingAction === "Deleting";
  const isRestore = workspace.projectDataLifecycle === "restore_required";
  const isUnrecoverable = workspace.projectDataLifecycle === "unrecoverable";
  const primaryActionDisabled = isPending || (isUnrecoverable ? false : !workspace.canOpen && !workspace.canStart);
  const primaryActionLabel = workspace.pendingAction
    ? `${workspace.pendingAction}...`
    : isRestore
      ? "Restore required"
      : isUnrecoverable
        ? "Data unavailable"
        : workspace.projectDirectoryMissing
          ? "Project data missing"
          : workspace.canOpen
            ? "Open workspace"
            : workspace.canStart
              ? "Start workspace"
              : workspace.statusLabel;
  const snapshots = workspace.restorableSnapshotCount ?? 0;
  const snapshotBusyHere = snapshotBusyWorkspaceId === workspace.id;
  const saveDisabledReason = saveWorkspaceDisabledReason(workspace);
  const canSaveSnapshot = saveDisabledReason === null;
  const canDownloadSnapshot = !isPending && snapshots > 0;

  return (
    <Card
      className={`group overflow-hidden rounded-[28px] border-white/80 bg-white/88 shadow-[0_24px_65px_-42px_rgba(15,23,42,0.5)] transition ${
        isDeleting ? "opacity-75" : "hover:-translate-y-0.5 hover:shadow-[0_28px_75px_-42px_rgba(15,23,42,0.55)]"
      }`}
    >
      <CardHeader className="flex flex-row items-start justify-between gap-4 pb-4">
        <div className="space-y-3">
          <div className="space-y-2">
            <DetailedStatusBadge workspace={workspace} />
            {isRestore ? (
              <p className="text-xs font-medium uppercase tracking-wide text-amber-800">Restore required</p>
            ) : null}
            {isUnrecoverable ? (
              <p className="text-xs font-medium uppercase tracking-wide text-rose-800">Data not recoverable</p>
            ) : null}
          </div>
          <div>
            <h3 className="text-lg font-semibold text-slate-950">{workspace.name}</h3>
            <p className="mt-1 text-sm leading-6 text-slate-500">{workspace.description}</p>
            {workspace.statusDetail ? <p className="mt-2 text-sm leading-6 text-slate-600">{workspace.statusDetail}</p> : null}
          </div>
        </div>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition hover:border-slate-300 hover:text-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
              aria-label={`Open actions for ${workspace.name}`}
              disabled={isPending}
            >
              <MoreVertical className="h-4 w-4" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuItem onClick={() => onStop(String(workspace.id))} disabled={isPending || !workspace.canStop}>
              <Square className="h-4 w-4" />
              Stop
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => onRestart(String(workspace.id))} disabled={isPending || !workspace.canRestart}>
              <RotateCcw className="h-4 w-4" />
              {workspace.canStart ? "Start" : "Restart"}
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => onSaveWorkspace(String(workspace.id))}
              disabled={!canSaveSnapshot || snapshotBusyHere}
              title={!snapshotBusyHere && saveDisabledReason ? saveDisabledReason : undefined}
            >
              <div className="flex w-full flex-col gap-0.5">
                <span className="flex items-center gap-2">
                  {snapshotBusyHere ? <Loader2 className="h-4 w-4 shrink-0 animate-spin" /> : <Save className="h-4 w-4 shrink-0" />}
                  {snapshotBusyHere ? "Saving workspace…" : "Save workspace"}
                </span>
                {!snapshotBusyHere && saveDisabledReason ? (
                  <span className="pl-6 text-xs font-normal leading-snug text-slate-500">{saveDisabledReason}</span>
                ) : null}
              </div>
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => {
                void onDownload(String(workspace.id));
              }}
              disabled={!canDownloadSnapshot || snapshotBusyHere}
            >
              <Download className="h-4 w-4" />
              Download workspace files
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => onRunWorkflow(String(workspace.id))} disabled>
              <PlayCircle className="h-4 w-4" />
              Run CI/CD Workflow
            </DropdownMenuItem>
            {isRestore ? (
              <DropdownMenuItem onClick={() => setRecoverOpen(true)}>
                <ArchiveRestore className="h-4 w-4" />
                How to restore from snapshot…
              </DropdownMenuItem>
            ) : null}
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-rose-600 focus:text-rose-600"
              onClick={() => onDelete(String(workspace.id))}
              disabled={isPending || !workspace.canDelete}
            >
              <Trash2 className="h-4 w-4" />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </CardHeader>

      <CardContent className="space-y-5">
        <div className="grid gap-3 rounded-3xl bg-slate-50 p-4 text-sm text-slate-600">
          <div className="flex items-center justify-between gap-3">
            <span>Last opened</span>
            <span className="font-medium text-slate-900">{workspace.lastOpenedLabel}</span>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span>Last modified</span>
            <span className="font-medium text-slate-900">{workspace.lastModifiedLabel}</span>
          </div>
        </div>

        <button
          type="button"
          className={`flex w-full items-center justify-center gap-2 rounded-2xl px-4 py-3 text-sm font-medium text-white transition ${
            primaryActionDisabled
              ? "cursor-not-allowed bg-slate-400"
              : isUnrecoverable
                ? "bg-rose-700 hover:bg-rose-600"
                : workspace.canOpen
                  ? "bg-slate-950 hover:bg-slate-800"
                  : "bg-sky-700 hover:bg-sky-600"
          }`}
          disabled={primaryActionDisabled}
          onClick={() => {
            if (isUnrecoverable) {
              if (
                typeof window !== "undefined" &&
                !window.confirm(
                  `Permanently remove workspace "${workspace.name}"? This enqueues deletion of the workspace record and runtime cleanup.`,
                )
              ) {
                return;
              }
              onDelete(String(workspace.id));
              return;
            }
            if (workspace.canOpen) {
              onOpen(String(workspace.id));
              return;
            }
            if (workspace.canStart) {
              onRestart(String(workspace.id));
            }
          }}
        >
          {isDeleting || workspace.pendingAction === "Opening" ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {primaryActionLabel}
        </button>
      </CardContent>

      <Dialog open={recoverOpen} onOpenChange={setRecoverOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Restore from snapshot</DialogTitle>
            <DialogDescription asChild>
              <div className="space-y-3 text-sm leading-6 text-slate-600">
                <p>
                  The control plane cannot find persisted files for this workspace on the configured host path.
                  DevNest will not create an empty directory in place of lost data.
                </p>
                {snapshots > 0 ? (
                  <ol className="list-decimal space-y-2 pl-5">
                    <li>Stop the workspace if it is RUNNING.</li>
                    <li>
                      Choose an AVAILABLE snapshot and call{" "}
                      <code className="rounded bg-slate-100 px-1 py-0.5 text-xs">POST /snapshots/&lt;id&gt;/restore</code>{" "}
                      (authenticated). This recreates the project tree from the archive.
                    </li>
                    <li>Start the workspace again.</li>
                  </ol>
                ) : (
                  <p className="font-medium text-amber-800">
                    No AVAILABLE snapshots were found for this workspace. Recovery requires a host-level backup,
                    redeployed files under WORKSPACE_PROJECTS_BASE, or operator assistance; there is no automatic
                    repair path.
                  </p>
                )}
              </div>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <button
              type="button"
              className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50"
              onClick={() => setRecoverOpen(false)}
            >
              Close
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
