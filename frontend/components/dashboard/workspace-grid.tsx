import { WorkspaceCard } from "@/components/dashboard/workspace-card";
import { Workspace } from "@/types/workspace";

type WorkspaceGridProps = {
  workspaces: Workspace[];
  isLoading?: boolean;
  errorMessage?: string | null;
  onOpen: (id: string) => void;
  onStop: (id: string) => void;
  onRestart: (id: string) => void;
  onDelete: (id: string) => void;
  onDownload: (id: string) => void;
  onRunWorkflow: (id: string) => void;
};

export function WorkspaceGrid({
  workspaces,
  isLoading = false,
  errorMessage = null,
  onOpen,
  onStop,
  onRestart,
  onDelete,
  onDownload,
  onRunWorkflow,
}: WorkspaceGridProps) {
  if (isLoading) {
    return (
      <div className="rounded-[28px] border border-dashed border-slate-300 bg-white/70 px-6 py-14 text-center shadow-[0_20px_50px_-40px_rgba(15,23,42,0.45)]">
        <h2 className="text-xl font-semibold text-slate-900">Loading workspaces...</h2>
        <p className="mt-2 text-sm text-slate-600">Fetching your latest DevNest workspace state from the backend.</p>
      </div>
    );
  }

  if (errorMessage) {
    return (
      <div className="rounded-[28px] border border-dashed border-rose-200 bg-rose-50/80 px-6 py-14 text-center shadow-[0_20px_50px_-40px_rgba(15,23,42,0.45)]">
        <h2 className="text-xl font-semibold text-rose-900">Unable to load workspaces</h2>
        <p className="mt-2 text-sm text-rose-700">{errorMessage}</p>
      </div>
    );
  }

  if (workspaces.length === 0) {
    return (
      <div className="rounded-[28px] border border-dashed border-slate-300 bg-white/70 px-6 py-14 text-center shadow-[0_20px_50px_-40px_rgba(15,23,42,0.45)]">
        <h2 className="text-xl font-semibold text-slate-900">No workspaces found</h2>
        <p className="mt-2 text-sm text-slate-600">Try a different search or create a fresh workspace to populate the grid.</p>
      </div>
    );
  }

  return (
    <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
      {workspaces.map((workspace) => (
        <WorkspaceCard
          key={workspace.id}
          workspace={workspace}
          onOpen={onOpen}
          onDelete={onDelete}
          onDownload={onDownload}
          onRestart={onRestart}
          onRunWorkflow={onRunWorkflow}
          onStop={onStop}
        />
      ))}
    </div>
  );
}
