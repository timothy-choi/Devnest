import { CloudCog, FolderKanban, TerminalSquare } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";

const features = [
  {
    title: "Browser-based IDE",
    description: "Jump into focused work from any machine with a polished cloud workspace shell.",
    icon: TerminalSquare,
  },
  {
    title: "Persistent workspaces",
    description: "Keep projects warm, organized, and ready to reopen without losing momentum.",
    icon: FolderKanban,
  },
  {
    title: "Optional integrations",
    description: "Prepare for CI/CD, AI tools, and terminal access without committing to backend wiring yet.",
    icon: CloudCog,
  },
];

export function FeatureHighlights() {
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      {features.map((feature) => (
        <Card
          key={feature.title}
          className="border-white/70 bg-white/75 shadow-[0_20px_45px_-35px_rgba(15,23,42,0.55)] backdrop-blur"
        >
          <CardContent className="space-y-3 p-5">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-slate-950 text-white">
              <feature.icon className="h-5 w-5" />
            </div>
            <div className="space-y-1.5">
              <h3 className="font-semibold text-slate-950">{feature.title}</h3>
              <p className="text-sm leading-6 text-slate-600">{feature.description}</p>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
