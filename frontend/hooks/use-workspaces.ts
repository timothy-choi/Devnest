"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { browserApi } from "@/lib/api/browser-client";
import { ApiError } from "@/lib/api/error";
import { getApiBaseUrl } from "@/lib/env";
import { WorkspaceFormValues } from "@/lib/validators";
import { toWorkspace } from "@/lib/workspace-mappers";
import { ProjectDataLifecycle, Workspace } from "@/types/workspace";

export type DashboardWorkspaceSection = "active" | "restore_required" | "unrecoverable";

export function useWorkspaces() {
  const queryClient = useQueryClient();
  const openInFlight = useRef<Set<number>>(new Set());
  const [query, setQuery] = useState("");
  const [dashboardSection, setDashboardSection] = useState<DashboardWorkspaceSection>("active");
  const [isCreateDialogOpen, setCreateDialogOpen] = useState(false);
  const [optimisticWorkspaces, setOptimisticWorkspaces] = useState<Workspace[]>([]);
  const [hiddenDeletedIds, setHiddenDeletedIds] = useState<number[]>([]);
  const [createError, setCreateError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [snapshotNotice, setSnapshotNotice] = useState<string | null>(null);
  const [snapshotBusyWorkspaceId, setSnapshotBusyWorkspaceId] = useState<number | null>(null);
  const [snapshotPollingWorkspaceIds, setSnapshotPollingWorkspaceIds] = useState<number[]>([]);
  const snapshotPollingWorkspaceIdsRef = useRef<number[]>([]);
  snapshotPollingWorkspaceIdsRef.current = snapshotPollingWorkspaceIds;

  useEffect(() => {
    const refreshWorkspacesAfterIdeReturn = () => {
      queryClient.setQueryData<Workspace[]>(["workspaces"], (current = []) =>
        current.map((workspace) =>
          workspace.pendingAction === "Opening"
            ? {
                ...workspace,
                pendingAction: null,
                isBusy: false,
                canOpen:
                  workspace.rawStatus === "RUNNING" &&
                  !(workspace.reopenIssues && workspace.reopenIssues.length > 0) &&
                  workspace.projectDataLifecycle !== "restore_required" &&
                  workspace.projectDataLifecycle !== "unrecoverable",
                canStart:
                  workspace.rawStatus === "STOPPED" &&
                  workspace.projectDataLifecycle !== "restore_required" &&
                  workspace.projectDataLifecycle !== "unrecoverable",
                canStop: workspace.rawStatus === "RUNNING",
                canRestart:
                  (workspace.rawStatus === "RUNNING" || workspace.rawStatus === "STOPPED") &&
                  workspace.projectDataLifecycle !== "restore_required" &&
                  workspace.projectDataLifecycle !== "unrecoverable",
                canDelete:
                  workspace.rawStatus === "RUNNING" ||
                  workspace.rawStatus === "STOPPED" ||
                  workspace.rawStatus === "ERROR",
                statusDetail:
                  workspace.rawStatus === "RUNNING"
                    ? null
                    : workspace.statusDetail,
              }
            : workspace,
        ),
      );
      void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      void queryClient.refetchQueries({ queryKey: ["workspaces"] });
    };

    refreshWorkspacesAfterIdeReturn();
    if (typeof window === "undefined") {
      return;
    }

    window.addEventListener("pageshow", refreshWorkspacesAfterIdeReturn);
    return () => {
      window.removeEventListener("pageshow", refreshWorkspacesAfterIdeReturn);
    };
  }, [queryClient]);

  const hasBusyOptimisticWorkspace = optimisticWorkspaces.some((workspace) => workspace.isBusy);

  const workspacesQuery = useQuery({
    queryKey: ["workspaces"],
    queryFn: async () => {
      const response = await browserApi.workspaces.list();
      return response.items
        .map(toWorkspace)
        .filter((workspace) => workspace.rawStatus !== "DELETED");
    },
    retry: false,
    refetchInterval: (data) => {
      const hasBusyWorkspace = Boolean(data?.some((workspace) => workspace.isBusy));
      const snapshotPolling = snapshotPollingWorkspaceIdsRef.current.length > 0;
      return hasBusyWorkspace || hasBusyOptimisticWorkspace || snapshotPolling ? 3000 : false;
    },
  });

  const createMutation = useMutation({
    mutationFn: (values: WorkspaceFormValues) => {
      return browserApi.workspaces.create(values);
    },
    onMutate: async (values) => {
      setCreateError(null);
      setActionError(null);
      setSnapshotNotice(null);

      const optimisticWorkspace: Workspace = {
        id: Date.now(),
        name: values.name,
        description: values.repositoryUrl
          ? `Repository seed requested: ${values.repositoryUrl}`
          : "Provisioning workspace through the DevNest control plane.",
        status: "pending",
        rawStatus: "PENDING",
        statusLabel: "Preparing capacity...",
        statusDetail: "Workspace accepted; placement and provisioning run asynchronously.",
        lastOpenedLabel: "Just now",
        lastModifiedLabel: "Just now",
        pendingAction: "Creating",
        isBusy: true,
        canOpen: false,
        canStart: false,
        canStop: false,
        canRestart: false,
        canDelete: false,
      };

      setOptimisticWorkspaces((current) => [optimisticWorkspace, ...current]);
      return optimisticWorkspace.id;
    },
    onSuccess: (response, _values, optimisticId) => {
      setOptimisticWorkspaces((current) => current.filter((workspace) => workspace.id !== optimisticId));
      queryClient.setQueryData<Workspace[]>(["workspaces"], (current = []) => [toWorkspace(response.workspace), ...current]);
    },
    onError: (error, _values, optimisticId) => {
      setOptimisticWorkspaces((current) => current.filter((workspace) => workspace.id !== optimisticId));
      if (error instanceof ApiError) {
        const s = error.status;
        if (s === 502 || s === 503 || s === 504) {
          setCreateError(
            "The service is temporarily unavailable. Refresh the dashboard in a moment — your workspace may still be provisioning.",
          );
          return;
        }
        setCreateError(error.detail);
        return;
      }
      setCreateError("Could not complete the create request. Try again or refresh the dashboard.");
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    },
  });

  const actionMutation = useMutation({
    mutationFn: async ({ id, action }: { id: number; action: "stop" | "restart" | "delete" }) => {
      if (action === "stop") {
        await browserApi.workspaces.stop(id);
      } else if (action === "restart") {
        await browserApi.workspaces.restart(id);
      } else {
        await browserApi.workspaces.remove(id);
      }
    },
    onMutate: async ({ id, action }) => {
      setActionError(null);
      setSnapshotNotice(null);
      const previousWorkspaces = queryClient.getQueryData<Workspace[]>(["workspaces"]) || [];
      queryClient.setQueryData<Workspace[]>(["workspaces"], (current = []) =>
        current.map((workspace) =>
          workspace.id === id
            ? {
                ...workspace,
                pendingAction: action === "stop" ? "Stopping" : action === "restart" ? "Restarting" : "Deleting",
                status:
                  action === "restart"
                    ? "restarting"
                    : action === "stop"
                      ? "setting-up"
                      : workspace.status,
                rawStatus: action === "restart" ? "RESTARTING" : action === "stop" ? "STOPPING" : "DELETING",
                statusLabel: action === "restart" ? "Restarting..." : action === "stop" ? "Stopping..." : "Deleting...",
                statusDetail:
                  action === "restart"
                    ? "Restart requested and currently being applied."
                    : action === "stop"
                      ? "Stop requested and currently being applied."
                      : "Delete accepted and waiting for the queued job to finish.",
                isBusy: true,
                canStop: false,
                canRestart: false,
                canDelete: false,
              }
            : workspace,
        ),
      );
      return { previousWorkspaces };
    },
    onSuccess: (_response, variables) => {
      if (variables.action === "delete") {
        setHiddenDeletedIds((current) => (current.includes(variables.id) ? current : [...current, variables.id]));
        queryClient.setQueryData<Workspace[]>(["workspaces"], (current = []) =>
          current.filter((workspace) => workspace.id !== variables.id),
        );
      }
    },
    onError: (error, _variables, context) => {
      if (context?.previousWorkspaces) {
        queryClient.setQueryData<Workspace[]>(["workspaces"], context.previousWorkspaces);
      }
      setActionError(error instanceof ApiError ? error.detail : "Unable to update the workspace right now.");
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    },
  });

  const workspaces = useMemo(() => {
    return [...optimisticWorkspaces, ...(workspacesQuery.data || [])].filter(
      (workspace) => workspace.rawStatus !== "DELETED" && !hiddenDeletedIds.includes(workspace.id),
    );
  }, [hiddenDeletedIds, optimisticWorkspaces, workspacesQuery.data]);

  const lifecycleOf = (w: Workspace): ProjectDataLifecycle => w.projectDataLifecycle ?? "ok";

  const activeDashboardWorkspaces = useMemo(() => {
    return workspaces.filter((w) => {
      const life = lifecycleOf(w);
      return life !== "restore_required" && life !== "unrecoverable";
    });
  }, [workspaces]);

  const restoreRequiredList = useMemo(
    () => workspaces.filter((w) => lifecycleOf(w) === "restore_required"),
    [workspaces],
  );

  const unrecoverableList = useMemo(
    () => workspaces.filter((w) => lifecycleOf(w) === "unrecoverable"),
    [workspaces],
  );

  const sectionWorkspaces = useMemo(() => {
    if (dashboardSection === "restore_required") {
      return restoreRequiredList;
    }
    if (dashboardSection === "unrecoverable") {
      return unrecoverableList;
    }
    return activeDashboardWorkspaces;
  }, [activeDashboardWorkspaces, dashboardSection, restoreRequiredList, unrecoverableList]);

  const filteredWorkspaces = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();

    if (!normalizedQuery) {
      return sectionWorkspaces;
    }

    return sectionWorkspaces.filter((workspace) => {
      return (
        workspace.name.toLowerCase().includes(normalizedQuery) ||
        workspace.description.toLowerCase().includes(normalizedQuery)
      );
    });
  }, [query, sectionWorkspaces]);

  const openWorkspace = async (id: string) => {
    const workspaceId = Number(id);
    if (!Number.isFinite(workspaceId)) {
      setActionError("Invalid workspace id.");
      return;
    }
    if (openInFlight.current.has(workspaceId)) {
      return;
    }
    openInFlight.current.add(workspaceId);

    setActionError(null);
    setSnapshotNotice(null);
    const previousWorkspaces = queryClient.getQueryData<Workspace[]>(["workspaces"]) || [];
    queryClient.setQueryData<Workspace[]>(["workspaces"], (current = []) =>
      current.map((workspace) =>
        workspace.id === workspaceId
          ? {
              ...workspace,
              pendingAction: "Opening",
              statusDetail: "Preparing workspace session and redirecting to the IDE.",
              isBusy: true,
              canOpen: false,
              canStart: false,
              canStop: false,
              canRestart: false,
              canDelete: false,
            }
          : workspace,
      ),
    );

    try {
      const detail = await browserApi.workspaces.get(workspaceId);
      if ((detail.status || "").toUpperCase() !== "RUNNING") {
        throw new ApiError(
          409,
          "This workspace is not running yet. Start it from the dashboard, wait until it is RUNNING, then open again.",
        );
      }
      const diskLife = detail.project_data_lifecycle ?? "ok";
      if (diskLife === "restore_required" || diskLife === "unrecoverable") {
        throw new ApiError(
          409,
          detail.project_data_user_message ||
            "Workspace project data is not available on the execution host.",
        );
      }
      const reopenBlockers = detail.reopen_issues?.length
        ? detail.reopen_issues
        : detail.reopenIssues ?? [];
      if (reopenBlockers.length > 0) {
        throw new ApiError(409, reopenBlockers.join("; "));
      }

      const maxAttachAttempts = 8;
      let attach: Awaited<ReturnType<typeof browserApi.workspaces.attach>> | null = null;
      for (let attempt = 0; attempt < maxAttachAttempts; attempt++) {
        try {
          attach = await browserApi.workspaces.attach(workspaceId);
          break;
        } catch (err) {
          const transientDetail =
            /retry shortly|not ready|reconcile job was queued|timeout|traefik|gateway edge|ide upstream|restart workspace/i;
          const isTransient =
            err instanceof ApiError &&
            ((err.status === 503 && transientDetail.test(err.detail)) ||
              (err.status === 409 && transientDetail.test(err.detail)));
          if (isTransient && attempt < maxAttachAttempts - 1) {
            await new Promise((r) => setTimeout(r, 180 + attempt * 140));
            continue;
          }
          throw err;
        }
      }
      if (!attach) {
        throw new ApiError(502, "Attach did not return a response.");
      }
      if (!attach.accepted) {
        throw new ApiError(
          409,
          attach.issues?.length ? attach.issues.join("; ") : "Attach was not accepted for this workspace.",
        );
      }

      const gatewayUrl = (attach.gateway_url || "").trim();
      if (!gatewayUrl) {
        throw new ApiError(502, "No gateway URL was returned for this workspace.");
      }

      const browserWindow = typeof globalThis !== "undefined" ? globalThis.window : undefined;
      if (browserWindow) {
        browserWindow.sessionStorage.setItem("devnestWorkspaceReturnTarget", "/dashboard");
        const dashboardUrl = new URL("/dashboard?workspaceReturn=1", browserWindow.location.origin).toString();
        browserWindow.history.pushState({ devnestWorkspaceReturn: true }, "", dashboardUrl);
        browserWindow.location.assign(gatewayUrl);
        return;
      }

      globalThis.location?.assign(gatewayUrl);
      return;
    } catch (error) {
      queryClient.setQueryData<Workspace[]>(["workspaces"], previousWorkspaces);
      void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      setActionError(error instanceof ApiError ? error.detail : "Unable to open the workspace right now.");
    } finally {
      openInFlight.current.delete(workspaceId);
    }
  };

  const saveWorkspace = async (id: string) => {
    const workspaceId = Number(id);
    if (!Number.isFinite(workspaceId)) {
      setActionError("Invalid workspace id.");
      return;
    }
    setActionError(null);
    setSnapshotNotice(null);
    setSnapshotBusyWorkspaceId(workspaceId);
    setSnapshotPollingWorkspaceIds((current) =>
      current.includes(workspaceId) ? current : [...current, workspaceId],
    );
    setSnapshotNotice("Saving workspace…");

    const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

    try {
      const stamp = new Date().toISOString().slice(0, 19).replace("T", " ");
      const accepted = await browserApi.workspaces.createSnapshot(workspaceId, {
        name: `Dashboard save ${stamp}`,
        description: "Saved from DevNest dashboard",
      });
      const snapshotId = accepted.snapshot_id;

      const maxAttempts = 90;
      for (let attempt = 0; attempt < maxAttempts; attempt++) {
        if (attempt > 0) {
          await sleep(2000);
        }
        const rows = await browserApi.workspaces.listSnapshots(workspaceId);
        const row = rows.find((r) => r.workspace_snapshot_id === snapshotId);
        const st = (row?.status || "").toUpperCase();
        if (st === "AVAILABLE") {
          queryClient.setQueryData<Workspace[]>(["workspaces"], (current = []) =>
            current.map((workspace) =>
              workspace.id === workspaceId
                ? {
                    ...workspace,
                    restorableSnapshotCount: Math.max(workspace.restorableSnapshotCount ?? 0, 1),
                  }
                : workspace,
            ),
          );
          await queryClient.invalidateQueries({ queryKey: ["workspaces"] });
          setSnapshotNotice("Workspace saved.");
          return;
        }
        if (st === "FAILED") {
          throw new ApiError(500, "Snapshot job failed. Check workspace worker logs or try again.");
        }
      }
      throw new ApiError(
        504,
        "Snapshot is taking longer than expected. Refresh the dashboard in a moment; the job may still complete.",
      );
    } catch (error) {
      setSnapshotNotice(null);
      setActionError(error instanceof ApiError ? error.detail : "Could not start snapshot.");
    } finally {
      setSnapshotPollingWorkspaceIds((current) => current.filter((wid) => wid !== workspaceId));
      setSnapshotBusyWorkspaceId(null);
    }
  };

  const downloadWorkspace = async (id: string) => {
    const workspaceId = Number(id);
    if (!Number.isFinite(workspaceId)) {
      setActionError("Invalid workspace id.");
      return;
    }
    setActionError(null);
    setSnapshotNotice(null);
    setSnapshotBusyWorkspaceId(workspaceId);
    try {
      const metaRes = await fetch(`/api/workspaces/${workspaceId}/snapshot-archive-download`, {
        method: "GET",
        credentials: "same-origin",
      });
      if (!metaRes.ok) {
        let detail = metaRes.statusText;
        try {
          const errBody = (await metaRes.json()) as { detail?: string };
          if (typeof errBody.detail === "string") {
            detail = errBody.detail;
          }
        } catch {
          // ignore non-JSON error bodies
        }
        throw new ApiError(metaRes.status, detail);
      }
      const offer = (await metaRes.json()) as {
        mode: "presigned_s3" | "backend_direct";
        filename: string;
        expires_in: number;
        presigned_url?: string | null;
        relative_url?: string | null;
      };

      let downloadHref: string;
      if (offer.mode === "presigned_s3" && offer.presigned_url) {
        downloadHref = offer.presigned_url;
      } else if (offer.mode === "backend_direct" && offer.relative_url) {
        const base = getApiBaseUrl().replace(/\/$/, "");
        const path = offer.relative_url.startsWith("/") ? offer.relative_url : `/${offer.relative_url}`;
        downloadHref = `${base}${path}`;
      } else {
        throw new ApiError(502, "Invalid download offer from server.");
      }

      const anchor = document.createElement("a");
      anchor.href = downloadHref;
      anchor.download = offer.filename;
      anchor.rel = "noopener noreferrer";
      anchor.target = "_blank";
      document.body.appendChild(anchor);
      try {
        anchor.click();
      } catch (clickErr) {
        const blockedProgrammaticDownload =
          clickErr instanceof DOMException &&
          (clickErr.name === "NotAllowedError" || clickErr.name === "SecurityError");
        if (!blockedProgrammaticDownload) {
          throw clickErr;
        }
        window.open(downloadHref, "_blank", "noopener,noreferrer");
      } finally {
        anchor.remove();
      }
      setActionError(null);
      setSnapshotNotice(null);
    } catch (error) {
      setSnapshotNotice(null);
      setActionError(error instanceof ApiError ? error.detail : "Could not download the snapshot archive.");
    } finally {
      setSnapshotBusyWorkspaceId(null);
    }
  };

  return {
    workspaces,
    filteredWorkspaces,
    dashboardSection,
    setDashboardSection,
    activeDashboardWorkspaces,
    restoreRequiredList,
    unrecoverableList,
    query,
    setQuery,
    isCreateDialogOpen,
    setCreateDialogOpen,
    isLoading: workspacesQuery.isLoading,
    errorMessage: workspacesQuery.error instanceof ApiError ? workspacesQuery.error.detail : null,
    isCreating: createMutation.isLoading,
    createError,
    actionError,
    snapshotNotice,
    snapshotBusyWorkspaceId,
    hasBusyWorkspace: workspaces.some((workspace) => workspace.isBusy),
    openCreateDialog: () => setCreateDialogOpen(true),
    createWorkspace: async (values: WorkspaceFormValues) => {
      await createMutation.mutateAsync(values);
    },
    openWorkspace,
    stopWorkspace: async (id: string) => {
      await actionMutation.mutateAsync({ id: Number(id), action: "stop" });
    },
    restartWorkspace: async (id: string) => {
      await actionMutation.mutateAsync({ id: Number(id), action: "restart" });
    },
    deleteWorkspace: async (id: string) => {
      await actionMutation.mutateAsync({ id: Number(id), action: "delete" });
    },
    downloadWorkspace,
    saveWorkspace,
    runWorkflow: async () => undefined,
  };
}
