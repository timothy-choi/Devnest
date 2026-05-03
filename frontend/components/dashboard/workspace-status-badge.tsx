import { AlertTriangle, Loader2, PauseCircle, RotateCcw, Rocket } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Workspace, WorkspaceStatus } from "@/types/workspace";

const statusConfig: Record<
  WorkspaceStatus,
  {
    label: string;
    className: string;
    icon: typeof Loader2;
  }
> = {
  "setting-up": {
    label: "Setting Up",
    className: "bg-sky-50 text-sky-700 ring-sky-200",
    icon: Loader2,
  },
  pending: {
    label: "Preparing Capacity",
    className: "bg-cyan-50 text-cyan-700 ring-cyan-200",
    icon: Loader2,
  },
  running: {
    label: "Running",
    className: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    icon: Rocket,
  },
  stopped: {
    label: "Stopped",
    className: "bg-slate-100 text-slate-700 ring-slate-200",
    icon: PauseCircle,
  },
  restarting: {
    label: "Restarting",
    className: "bg-amber-50 text-amber-700 ring-amber-200",
    icon: RotateCcw,
  },
  error: {
    label: "Error",
    className: "bg-rose-50 text-rose-700 ring-rose-200",
    icon: AlertTriangle,
  },
};

export function StatusBadge({ status }: { status: WorkspaceStatus }) {
  const config = statusConfig[status];
  const Icon = config.icon;

  return (
    <Badge className={`gap-2 rounded-full px-3 py-1 font-medium ring-1 ring-inset ${config.className}`}>
      <Icon className={`h-3.5 w-3.5 ${status === "setting-up" || status === "pending" || status === "restarting" ? "animate-spin" : ""}`} />
      {config.label}
    </Badge>
  );
}

export function DetailedStatusBadge({ workspace }: { workspace: Workspace }) {
  const config = statusConfig[workspace.status];
  const Icon = config.icon;

  return (
    <Badge className={`gap-2 rounded-full px-3 py-1 font-medium ring-1 ring-inset ${config.className}`}>
      <Icon className={`h-3.5 w-3.5 ${workspace.isBusy ? "animate-spin" : ""}`} />
      {workspace.statusLabel}
    </Badge>
  );
}
