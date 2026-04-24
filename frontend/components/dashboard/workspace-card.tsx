"use client";

import { useState } from "react";
import { ArchiveRestore, Download, Loader2, MoreVertical, PlayCircle, RotateCcw, Square, Trash2 } from "lucide-react";

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

type WorkspaceCardProps = {
  workspace: Workspace;
  onOpen: (id: string) => void;
  onStop: (id: string) => void;
  onRestart: (id: string) => void;
  onDelete: (id: string) => void;
  onDownload: (id: string) => void;
  onRunWorkflow: (id: string) => void;
};

export function WorkspaceCard({
  workspace,
  onOpen,
  onStop,
  onRestart,
  onDelete,
  onDownload,
  onRunWorkflow,
}: WorkspaceCardProps) {
  const [recoverOpen, setRecoverOpen] = useState(false);
  const isPending = workspace.pendingAction !== null;
  const isDeleting = workspace.pendingAction === "Deleting";
  const primaryActionDisabled = isPending || (!workspace.canOpen && !workspace.canStart);
  const primaryActionLabel = workspace.pendingAction
    ? `${workspace.pendingAction}...`
    : workspace.projectDirectoryMissing
      ? "Project data missing"
      : workspace.canOpen
        ? "Open workspace"
        : workspace.canStart
          ? "Start workspace"
          : workspace.statusLabel;
  const snapshots = workspace.restorableSnapshotCount ?? 0;

  return (
    <Card
      className={`group overflow-hidden rounded-[28px] border-white/80 bg-white/88 shadow-[0_24px_65px_-42px_rgba(15,23,42,0.5)] transition ${
        isDeleting ? "opacity-75" : "hover:-translate-y-0.5 hover:shadow-[0_28px_75px_-42px_rgba(15,23,42,0.55)]"
      }`}
    >
      <CardHeader className="flex flex-row items-start justify-between gap-4 pb-4">
        <div className="space-y-3">
          <DetailedStatusBadge workspace={workspace} />
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
            <DropdownMenuItem onClick={() => onDownload(String(workspace.id))} disabled>
              <Download className="h-4 w-4" />
              Download Project
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => onRunWorkflow(String(workspace.id))} disabled>
              <PlayCircle className="h-4 w-4" />
              Run CI/CD Workflow
            </DropdownMenuItem>
            {workspace.projectDirectoryMissing ? (
              <DropdownMenuItem onClick={() => setRecoverOpen(true)}>
                <ArchiveRestore className="h-4 w-4" />
                Recover project files…
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
              : workspace.canOpen
                ? "bg-slate-950 hover:bg-slate-800"
                : "bg-sky-700 hover:bg-sky-600"
          }`}
          disabled={primaryActionDisabled}
          onClick={() => {
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
            <DialogTitle>Project directory missing</DialogTitle>
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
