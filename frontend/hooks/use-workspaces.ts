"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { browserApi } from "@/lib/api/browser-client";
import { ApiError } from "@/lib/api/error";
import { WorkspaceFormValues } from "@/lib/validators";
import { toWorkspace } from "@/lib/workspace-mappers";
import { Workspace } from "@/types/workspace";

export function useWorkspaces() {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [isCreateDialogOpen, setCreateDialogOpen] = useState(false);
  const [optimisticWorkspaces, setOptimisticWorkspaces] = useState<Workspace[]>([]);
  const [createError, setCreateError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const hasBusyOptimisticWorkspace = optimisticWorkspaces.some((workspace) => workspace.isBusy);

  const workspacesQuery = useQuery({
    queryKey: ["workspaces"],
    queryFn: async () => {
      const response = await browserApi.workspaces.list();
      return response.items.map(toWorkspace);
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
      if (action === "delete") {
        queryClient.setQueryData<Workspace[]>(["workspaces"], (current = []) =>
          current.filter((workspace) => workspace.id !== id),
        );
        return { previousWorkspaces };
      }
      queryClient.setQueryData<Workspace[]>(["workspaces"], (current = []) =>
        current.map((workspace) =>
          workspace.id === id
            ? {
                ...workspace,
                pendingAction: action === "stop" ? "Stopping" : action === "restart" ? "Restarting" : "Deleting",
                status: action === "restart" ? "restarting" : action === "stop" ? "setting-up" : workspace.status,
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
    return [...optimisticWorkspaces, ...(workspacesQuery.data || [])];
  }, [optimisticWorkspaces, workspacesQuery.data]);

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
