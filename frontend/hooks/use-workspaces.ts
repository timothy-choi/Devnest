"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { createWorkspaceFromValues, initialMockWorkspaces } from "@/lib/mock-data";
import { WorkspaceFormValues } from "@/lib/validators";
import { Workspace } from "@/types/workspace";

export function useWorkspaces() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>(initialMockWorkspaces);
  const [query, setQuery] = useState("");
  const [isCreateDialogOpen, setCreateDialogOpen] = useState(false);
  const timersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  useEffect(() => {
    return () => {
      Object.values(timersRef.current).forEach(clearTimeout);
    };
  }, []);

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

  const transitionWorkspace = (id: string, status: Workspace["status"]) => {
    setWorkspaces((current) =>
      current.map((workspace) =>
        workspace.id === id
          ? {
              ...workspace,
              status,
              lastModifiedLabel: "Just now",
            }
          : workspace,
      ),
    );
  };

  const createWorkspace = (values: WorkspaceFormValues) => {
    const workspace = createWorkspaceFromValues(values);

    setWorkspaces((current) => [workspace, ...current]);

    timersRef.current[workspace.id] = setTimeout(() => {
      transitionWorkspace(workspace.id, "running");
      delete timersRef.current[workspace.id];
    }, 2200);
  };

  const stopWorkspace = (id: string) => {
    transitionWorkspace(id, "stopped");
  };

  const restartWorkspace = (id: string) => {
    transitionWorkspace(id, "restarting");

    if (timersRef.current[id]) {
      clearTimeout(timersRef.current[id]);
    }

    timersRef.current[id] = setTimeout(() => {
      transitionWorkspace(id, "running");
      delete timersRef.current[id];
    }, 1800);
  };

  const deleteWorkspace = (id: string) => {
    if (timersRef.current[id]) {
      clearTimeout(timersRef.current[id]);
      delete timersRef.current[id];
    }

    setWorkspaces((current) => current.filter((workspace) => workspace.id !== id));
  };

  const runWorkflow = (id: string) => {
    transitionWorkspace(id, "restarting");

    if (timersRef.current[id]) {
      clearTimeout(timersRef.current[id]);
    }

    timersRef.current[id] = setTimeout(() => {
      transitionWorkspace(id, "running");
      delete timersRef.current[id];
    }, 1400);
  };

  const downloadWorkspace = (id: string) => {
    setWorkspaces((current) =>
      current.map((workspace) =>
        workspace.id === id
          ? {
              ...workspace,
              lastOpenedLabel: "Download prepared",
            }
          : workspace,
      ),
    );
  };

  return {
    workspaces,
    filteredWorkspaces,
    query,
    setQuery,
    isCreateDialogOpen,
    setCreateDialogOpen,
    openCreateDialog: () => setCreateDialogOpen(true),
    createWorkspace,
    stopWorkspace,
    restartWorkspace,
    deleteWorkspace,
    downloadWorkspace,
    runWorkflow,
  };
}
