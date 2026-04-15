"use client";

import Link from "next/link";
import { Download, MoreVertical, PlayCircle, RotateCcw, Square, Trash2 } from "lucide-react";

import { DetailedStatusBadge } from "@/components/dashboard/workspace-status-badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Workspace } from "@/types/workspace";

type WorkspaceCardProps = {
  workspace: Workspace;
  onStop: (id: string) => void;
  onRestart: (id: string) => void;
  onDelete: (id: string) => void;
  onDownload: (id: string) => void;
  onRunWorkflow: (id: string) => void;
};

export function WorkspaceCard({
  workspace,
  onStop,
  onRestart,
  onDelete,
  onDownload,
  onRunWorkflow,
}: WorkspaceCardProps) {
  const isPending = workspace.pendingAction !== null;

  return (
    <Card className="group overflow-hidden rounded-[28px] border-white/80 bg-white/88 shadow-[0_24px_65px_-42px_rgba(15,23,42,0.5)] transition hover:-translate-y-0.5 hover:shadow-[0_28px_75px_-42px_rgba(15,23,42,0.55)]">
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
              className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition hover:border-slate-300 hover:text-slate-800"
              aria-label={`Open actions for ${workspace.name}`}
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
              Restart
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => onDownload(String(workspace.id))} disabled>
              <Download className="h-4 w-4" />
              Download Project
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => onRunWorkflow(String(workspace.id))} disabled>
              <PlayCircle className="h-4 w-4" />
              Run CI/CD Workflow
            </DropdownMenuItem>
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

        <Link href={`/workspace/${workspace.id}`}>
          <a
            className={`flex items-center justify-center rounded-2xl px-4 py-3 text-sm font-medium text-white transition ${
              workspace.isBusy ? "cursor-not-allowed bg-slate-400" : "bg-slate-950 hover:bg-slate-800"
            }`}
            aria-disabled={workspace.isBusy}
            onClick={(event) => {
              if (workspace.isBusy) {
                event.preventDefault();
              }
            }}
          >
            {workspace.pendingAction ? `${workspace.pendingAction}...` : workspace.isBusy ? workspace.statusLabel : "Open workspace"}
          </a>
        </Link>
      </CardContent>
    </Card>
  );
}
