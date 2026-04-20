import { z } from "zod";

export const workspaceFormSchema = z
  .object({
    name: z.string().min(3, "Use at least 3 characters for the workspace name."),
    repositoryUrl: z.union([z.literal(""), z.string().url("Enter a valid repository URL.")]).optional(),
    aiProvider: z.union([z.literal(""), z.enum(["openai", "anthropic"])]),
    aiApiKey: z.string().optional(),
    aiModel: z.string().optional(),
  })
  .superRefine((values, ctx) => {
    const provider = values.aiProvider || "";
    const key = (values.aiApiKey || "").trim();
    const model = (values.aiModel || "").trim();

    if (!provider && (key || model)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["aiProvider"],
        message: "Choose an AI provider before adding workspace AI credentials.",
      });
    }

    if (provider && !key) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["aiApiKey"],
        message: "Add an API key for the selected AI provider.",
      });
    }
  });

export type WorkspaceFormValues = z.infer<typeof workspaceFormSchema>;
