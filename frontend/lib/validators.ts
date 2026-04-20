import { z } from "zod";

export const workspaceFormSchema = z.object({
  name: z.string().min(3, "Use at least 3 characters for the workspace name."),
  repositoryUrl: z.union([z.literal(""), z.string().url("Enter a valid repository URL.")]).optional(),
  enableCiCd: z.boolean(),
});

export type WorkspaceFormValues = z.infer<typeof workspaceFormSchema>;
