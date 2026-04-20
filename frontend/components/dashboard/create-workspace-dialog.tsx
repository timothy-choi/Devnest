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

type CreateWorkspaceDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreateWorkspace: (values: WorkspaceFormValues) => Promise<void>;
  isSubmitting?: boolean;
  submitError?: string | null;
};

export function CreateWorkspaceDialog({
  open,
  onOpenChange,
  onCreateWorkspace,
  isSubmitting = false,
  submitError = null,
}: CreateWorkspaceDialogProps) {
  const form = useForm<WorkspaceFormValues>({
    resolver: zodResolver(workspaceFormSchema),
    defaultValues: {
      name: "",
      repositoryUrl: "",
      aiProvider: "",
      aiApiKey: "",
      aiModel: "",
    },
  });

  const onSubmit = async (values: WorkspaceFormValues) => {
    await onCreateWorkspace(values);
    onOpenChange(false);
    form.reset();
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="top-4 flex max-h-[calc(100vh-2rem)] flex-col translate-y-0 gap-0 overflow-hidden p-0 sm:top-1/2 sm:max-h-[82vh] sm:max-w-lg sm:-translate-y-1/2">
        <DialogHeader className="border-b border-slate-200/80 px-6 py-5">
          <DialogTitle>Create a new workspace</DialogTitle>
          <DialogDescription>
            Every workspace includes a terminal plus a default AI-ready toolset. Optional repository and AI terminal
            setup can be configured here.
          </DialogDescription>
        </DialogHeader>

        <form className="flex min-h-0 flex-1 flex-col overflow-hidden" onSubmit={form.handleSubmit(onSubmit)}>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
            <div className="space-y-5">
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
                  <p className="text-sm text-slate-500">Optional. This is captured for the next repository integration step.</p>
                )}
              </div>

              <div className="rounded-3xl border border-slate-200 bg-white px-4 py-4 text-sm text-slate-600">
                <p className="font-medium text-slate-900">Included in every workspace</p>
                <p className="mt-2 leading-6">
                  Integrated terminal access plus preinstalled AI coding tools for GitHub Copilot, Copilot Chat, and
                  Continue. Users can still add more extensions inside code-server if they want.
                </p>
              </div>

              <div className="space-y-4 rounded-3xl border border-slate-200 bg-white px-4 py-4">
                <div className="space-y-1">
                  <p className="font-medium text-slate-900">Workspace AI terminal configuration</p>
                  <p className="text-sm leading-6 text-slate-600">
                    Optional. If you add a provider and API key, the built-in <code>devnest-ai</code> terminal helper
                    will be ready to use inside this workspace without manually exporting environment variables.
                  </p>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="ai-provider">AI Provider</Label>
                  <select
                    id="ai-provider"
                    className="flex h-11 w-full rounded-2xl border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                    {...form.register("aiProvider")}
                  >
                    <option value="">No default provider</option>
                    <option value="openai">OpenAI</option>
                    <option value="anthropic">Anthropic</option>
                  </select>
                  {form.formState.errors.aiProvider ? (
                    <p className="text-sm text-rose-600">{form.formState.errors.aiProvider.message}</p>
                  ) : null}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="ai-api-key">AI API Key</Label>
                  <Input
                    id="ai-api-key"
                    type="password"
                    placeholder="sk-... or sk-ant-..."
                    autoComplete="off"
                    {...form.register("aiApiKey")}
                  />
                  {form.formState.errors.aiApiKey ? (
                    <p className="text-sm text-rose-600">{form.formState.errors.aiApiKey.message}</p>
                  ) : (
                    <p className="text-sm text-slate-500">
                      Stored encrypted for this workspace and injected only at runtime so terminal AI commands can use it automatically.
                    </p>
                  )}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="ai-model">AI Model</Label>
                  <Input
                    id="ai-model"
                    placeholder={form.watch("aiProvider") === "anthropic" ? "claude-3-5-sonnet-latest" : "gpt-4.1-mini"}
                    {...form.register("aiModel")}
                  />
                  <p className="text-sm text-slate-500">
                    Optional. Leave blank to use the default model for the selected provider.
                  </p>
                </div>
              </div>
            </div>
          </div>

          <div className="sticky bottom-0 border-t border-slate-200/80 bg-white px-6 py-4">
            {submitError ? <p className="rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-700">{submitError}</p> : null}

            <DialogFooter className="mt-3 sm:justify-between">
              <Button type="button" variant="secondary" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
                Cancel
              </Button>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Creating..." : "Create Workspace"}
              </Button>
            </DialogFooter>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
