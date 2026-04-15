import { WorkspaceFormValues } from "@/lib/validators";
import { Workspace } from "@/types/workspace";

export const initialMockWorkspaces: Workspace[] = [
  {
    id: "workspace-1",
    name: "Design system refresh",
    description: "Landing page experiments, design tokens, and shared component polish.",
    status: "running",
    lastOpenedLabel: "2 hours ago",
    lastModifiedLabel: "15 minutes ago",
    repositoryUrl: "https://github.com/timothy-choi/devnest-design-refresh",
    features: {
      ciCd: true,
      aiTools: true,
      terminal: true,
    },
  },
  {
    id: "workspace-2",
    name: "Telemetry sandbox",
    description: "Event inspection and observability trials for system-level reliability work.",
    status: "setting-up",
    lastOpenedLabel: "Just now",
    lastModifiedLabel: "Just now",
    repositoryUrl: "https://github.com/timothy-choi/devnest-telemetry",
    features: {
      ciCd: true,
      aiTools: false,
      terminal: true,
    },
  },
  {
    id: "workspace-3",
    name: "Customer demo deck",
    description: "A stable preview environment prepared for stakeholder walkthroughs.",
    status: "stopped",
    lastOpenedLabel: "Yesterday",
    lastModifiedLabel: "2 days ago",
    repositoryUrl: "",
    features: {
      ciCd: false,
      aiTools: true,
      terminal: false,
    },
  },
  {
    id: "workspace-4",
    name: "Migration rehearsal",
    description: "Validating container setup, rollback paths, and preflight checks.",
    status: "restarting",
    lastOpenedLabel: "30 minutes ago",
    lastModifiedLabel: "1 minute ago",
    repositoryUrl: "https://github.com/timothy-choi/devnest-migration",
    features: {
      ciCd: true,
      aiTools: true,
      terminal: true,
    },
  },
  {
    id: "workspace-5",
    name: "Failing integration triage",
    description: "A mock error state for validating dashboard visuals and recovery flows.",
    status: "error",
    lastOpenedLabel: "Last week",
    lastModifiedLabel: "3 days ago",
    repositoryUrl: "https://github.com/timothy-choi/devnest-triage",
    features: {
      ciCd: true,
      aiTools: false,
      terminal: true,
    },
  },
];

export function createWorkspaceFromValues(values: WorkspaceFormValues): Workspace {
  const slug = values.name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");

  return {
    id: `workspace-${slug || "new"}-${Date.now()}`,
    name: values.name.trim(),
    description: values.repositoryUrl
      ? "Provisioning from the supplied repository with selected mock integrations."
      : "Provisioning a fresh workspace shell with your selected mock features.",
    status: "setting-up",
    lastOpenedLabel: "Just now",
    lastModifiedLabel: "Just now",
    repositoryUrl: values.repositoryUrl?.trim() ?? "",
    features: {
      ciCd: values.enableCiCd,
      aiTools: values.enableAiTools,
      terminal: values.enableTerminal,
    },
  };
}
