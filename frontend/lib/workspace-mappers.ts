import { WorkspaceRecord } from "@/lib/api/browser-client";
import { Workspace } from "@/types/workspace";

const BUSY_STATUSES = new Set([
  "CREATING",
  "STARTING",
  "STOPPING",
  "RESTARTING",
  "UPDATING",
  "DELETING",
]);

function formatRelativeDate(value: string | null | undefined) {
  if (!value) {
    return "Unavailable";
  }

  const date = new Date(value);
  const deltaMs = Date.now() - date.getTime();
  const deltaMinutes = Math.max(1, Math.round(deltaMs / 60000));

  if (deltaMinutes < 60) {
    return `${deltaMinutes} minute${deltaMinutes === 1 ? "" : "s"} ago`;
  }

  const deltaHours = Math.round(deltaMinutes / 60);
  if (deltaHours < 24) {
    return `${deltaHours} hour${deltaHours === 1 ? "" : "s"} ago`;
  }

  const deltaDays = Math.round(deltaHours / 24);
  if (deltaDays < 7) {
    return `${deltaDays} day${deltaDays === 1 ? "" : "s"} ago`;
  }

  return date.toLocaleDateString();
}

export function mapBackendStatus(status: string): Workspace["status"] {
  switch (status) {
    case "RUNNING":
      return "running";
    case "STOPPING":
    case "DELETING":
    case "DELETED":
      return "stopped";
    case "STOPPED":
      return "stopped";
    case "RESTARTING":
      return "restarting";
    case "ERROR":
      return "error";
    default:
      return "setting-up";
  }
}

function getStatusLabel(status: string) {
  switch (status) {
    case "CREATING":
      return "Setting up...";
    case "STARTING":
      return "Starting...";
    case "STOPPING":
      return "Stopping...";
    case "RESTARTING":
      return "Restarting...";
    case "UPDATING":
      return "Busy...";
    case "DELETING":
      return "Deleting...";
    case "RUNNING":
      return "Running";
    case "STOPPED":
      return "Stopped";
    case "ERROR":
      return "Failed";
    case "DELETED":
      return "Deleted";
    default:
      return "Setting up...";
  }
}

function getStatusDetail(record: WorkspaceRecord) {
  if (record.lastErrorMessage) {
    return record.lastErrorMessage;
  }

  if (record.statusReason) {
    return record.statusReason;
  }

  switch (record.status) {
    case "CREATING":
      return "Create accepted and waiting for a worker to process the queued job.";
    case "STARTING":
      return "Start requested and waiting for the worker/orchestrator to finish.";
    case "STOPPING":
      return "Stop requested and currently being applied.";
    case "RESTARTING":
      return "Restart requested and currently being applied.";
    case "UPDATING":
      return "Workspace update in progress.";
    case "DELETING":
      return "Delete accepted and waiting for the queued job to finish.";
    case "ERROR":
      return "Workspace entered an error state. Check backend worker/orchestrator logs.";
    default:
      return null;
  }
}

export function toWorkspace(record: WorkspaceRecord): Workspace {
  const isBusy = BUSY_STATUSES.has(record.status);
  const reopenIssues = record.reopenIssues ?? [];
  const hasReopenBlockers = reopenIssues.length > 0;
  const projectDirectoryMissing = reopenIssues.some((issue) =>
    /project directory is missing/i.test(issue),
  );
  const canOpen = record.status === "RUNNING" && !hasReopenBlockers;
  const canStart = record.status === "STOPPED" && !hasReopenBlockers;

  const baseDetail = getStatusDetail(record);
  const reopenDetail = hasReopenBlockers ? `Reopen blocked: ${reopenIssues.join("; ")}` : null;
  const statusDetail = [reopenDetail, baseDetail].filter(Boolean).join(" — ") || null;

  return {
    id: record.id,
    name: record.name,
    description:
      record.description ||
      record.statusReason ||
      record.lastErrorMessage ||
      "Workspace accepted by the control plane.",
    status: mapBackendStatus(record.status),
    rawStatus: record.status,
    statusLabel: getStatusLabel(record.status),
    statusDetail,
    lastOpenedLabel: formatRelativeDate(record.lastStarted || record.createdAt),
    lastModifiedLabel: formatRelativeDate(record.updatedAt),
    pendingAction: null,
    isBusy,
    canOpen,
    canStart,
    canStop: record.status === "RUNNING",
    canRestart:
      (record.status === "RUNNING" || record.status === "STOPPED") && !hasReopenBlockers,
    canDelete: record.status === "RUNNING" || record.status === "STOPPED" || record.status === "ERROR",
    reopenIssues: hasReopenBlockers ? reopenIssues : undefined,
    restorableSnapshotCount: record.restorableSnapshotCount,
    projectDirectoryMissing: projectDirectoryMissing || undefined,
  };
}
