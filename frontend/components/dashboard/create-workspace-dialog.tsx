"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";

import { workspaceFormSchema, WorkspaceFormValues } from "@/lib/validators";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

type CreateWorkspaceDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreateWorkspace: (values: WorkspaceFormValues) => void;
};

export function CreateWorkspaceDialog({
  open,
  onOpenChange,
  onCreateWorkspace,
}: CreateWorkspaceDialogProps) {
  const form = useForm<WorkspaceFormValues>({
    resolver: zodResolver(workspaceFormSchema),
    defaultValues: {
      name: "",
      repositoryUrl: "",
      enableCiCd: true,
      enableAiTools: true,
      enableTerminal: true,
    },
  });

  const onSubmit = (values: WorkspaceFormValues) => {
    onCreateWorkspace(values);
    onOpenChange(false);
    form.reset();
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Create a new workspace</DialogTitle>
          <DialogDescription>
            Start from mock data now, then swap this modal over to the real provisioning API in the next phase.
          </DialogDescription>
        </DialogHeader>

        <form className="space-y-6" onSubmit={form.handleSubmit(onSubmit)}>
          <div className="space-y-2">
            <Label htmlFor="workspace-name">Workspace Name</Label>
            <Input id="workspace-name" placeholder="Platform migration spike" {...form.register("name")} />
            {form.formState.errors.name ? (
              <p className="text-sm text-rose-600">{form.formState.errors.name.message}</p>
            ) : null}
          </div>

          <div className="space-y-2">
            <Label htmlFor="repository-url">Repository URL</Label>
            <Input
              id="repository-url"
              placeholder="https://github.com/org/repo"
              {...form.register("repositoryUrl")}
            />
            {form.formState.errors.repositoryUrl ? (
              <p className="text-sm text-rose-600">{form.formState.errors.repositoryUrl.message}</p>
            ) : (
              <p className="text-sm text-slate-500">Optional for now. This stays mock-only in the current foundation step.</p>
            )}
          </div>

          <div className="space-y-4 rounded-3xl bg-slate-50 p-4">
            <ToggleRow
              checked={form.watch("enableCiCd")}
              label="Enable CI/CD"
              description="Preconfigure workflow controls in the workspace shell."
              onCheckedChange={(checked) => form.setValue("enableCiCd", checked)}
            />
            <ToggleRow
              checked={form.watch("enableAiTools")}
              label="Enable AI Tools"
              description="Reserve UI space for AI-assisted developer tooling."
              onCheckedChange={(checked) => form.setValue("enableAiTools", checked)}
            />
            <ToggleRow
              checked={form.watch("enableTerminal")}
              label="Enable Terminal"
              description="Keep the future terminal integration visible in the workspace shape."
              onCheckedChange={(checked) => form.setValue("enableTerminal", checked)}
            />
          </div>

          <DialogFooter className="sm:justify-between">
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit">Create Workspace</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ToggleRow({
  checked,
  label,
  description,
  onCheckedChange,
}: {
  checked: boolean;
  label: string;
  description: string;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="space-y-1">
        <p className="font-medium text-slate-900">{label}</p>
        <p className="text-sm leading-6 text-slate-600">{description}</p>
      </div>
      <Switch checked={checked} onCheckedChange={onCheckedChange} />
    </div>
  );
}
