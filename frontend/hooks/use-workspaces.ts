"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { browserApi } from "@/lib/api/browser-client";
import { ApiError } from "@/lib/api/error";
import { WorkspaceFormValues } from "@/lib/validators";
import { toWorkspace } from "@/lib/workspace-mappers";
import { Workspace } from "@/types/workspace";

export function useWorkspaces() {
  const queryClient = useQueryClient();
  const openInFlight = useRef<Set<number>>(new Set());
  const [query, setQuery] = useState("");
  const [isCreateDialogOpen, setCreateDialogOpen] = useState(false);
  const [optimisticWorkspaces, setOptimisticWorkspaces] = useState<Workspace[]>([]);
  const [hiddenDeletedIds, setHiddenDeletedIds] = useState<number[]>([]);
  const [createError, setCreateError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    const clearStaleOpeningState = () => {
      queryClient.setQueryData<Workspace[]>(["workspaces"], (current = []) =>
        current.map((workspace) =>
          workspace.pendingAction === "Opening"
            ? {
                ...workspace,
                pendingAction: null,
                isBusy: false,
                canOpen:
                  workspace.rawStatus === "RUNNING" &&
                  !(workspace.reopenIssues && workspace.reopenIssues.length > 0),
                canStart: workspace.rawStatus === "STOPPED",
                canStop: workspace.rawStatus === "RUNNING",
                canRestart: workspace.rawStatus === "RUNNING" || workspace.rawStatus === "STOPPED",
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
    };

    clearStaleOpeningState();
    if (typeof window === "undefined") {
      return;
    }

    window.addEventListener("pageshow", clearStaleOpeningState);
    return () => {
      window.removeEventListener("pageshow", clearStaleOpeningState);
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
      return hasBusyWorkspace || hasBusyOptimisticWorkspace ? 5000 : false;
    },
  });

  const createMutation = useMutation({
    mutationFn: (values: WorkspaceFormValues) => {
      return browserApi.workspaces.create(values);
    },
    onMutate: async (values) => {
      setCreateError(null);
      setActionError(null);

      const optimisticWorkspace: Workspace = {
        id: Date.now(),
        name: values.name,
        description: values.repositoryUrl
          ? `Repository seed requested: ${values.repositoryUrl}`
          : "Provisioning workspace through the DevNest control plane.",
        status: "setting-up",
        rawStatus: "CREATING",
        statusLabel: "Setting up...",
        statusDetail: "Create accepted and waiting for a worker to process the queued job.",
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
      setCreateError(error instanceof ApiError ? error.detail : "Unable to create the workspace.");
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

  const filteredWorkspaces = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();

    if (!normalizedQuery) {
      return workspaces;
    }

    return workspaces.filter((workspace) => {
      return (
        workspace.name.toLowerCase().includes(normalizedQuery) ||
        workspace.description.toLowerCase().includes(normalizedQuery)
      );
    });
  }, [query, workspaces]);

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
          const isTransient =
            err instanceof ApiError &&
            err.status === 409 &&
            /retry shortly|not ready|reconcile job was queued|timeout/i.test(err.detail);
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

  return {
    workspaces,
    filteredWorkspaces,
    query,
    setQuery,
    isCreateDialogOpen,
    setCreateDialogOpen,
    isLoading: workspacesQuery.isLoading,
    errorMessage: workspacesQuery.error instanceof ApiError ? workspacesQuery.error.detail : null,
    isCreating: createMutation.isLoading,
    createError,
    actionError,
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
    downloadWorkspace: async () => undefined,
    runWorkflow: async () => undefined,
  };
}
