import Link from "next/link";
import { ArrowRight, Bot } from "lucide-react";

import { FeatureHighlights } from "@/components/marketing/feature-highlights";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export default function LandingPage() {
  return (
    <main className="relative overflow-hidden bg-[radial-gradient(circle_at_top_left,_rgba(59,130,246,0.16),_transparent_30%),radial-gradient(circle_at_bottom_right,_rgba(14,165,233,0.12),_transparent_28%),linear-gradient(180deg,_#f8fbff_0%,_#eef4ff_48%,_#f7f9fc_100%)]">
      <div className="mx-auto flex min-h-screen max-w-7xl flex-col px-6 py-8 lg:px-10">
        <header className="flex items-center justify-between rounded-full border border-white/70 bg-white/80 px-5 py-3 shadow-[0_12px_40px_-24px_rgba(15,23,42,0.45)] backdrop-blur">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-slate-950 text-sm font-semibold text-white">
              DN
            </div>
            <div>
              <p className="text-sm font-semibold text-slate-950">DevNest</p>
              <p className="text-xs text-slate-500">Workspaces without setup drag</p>
            </div>
          </div>
          <nav className="hidden items-center gap-2 md:flex">
            <Link href="/login">
              <a className={cn(buttonVariants({ variant: "ghost" }), "text-slate-700")}>Login</a>
            </Link>
            <Link href="/signup">
              <a className={buttonVariants()}>Sign Up</a>
            </Link>
          </nav>
        </header>

        <section className="grid flex-1 items-center gap-12 py-16 lg:grid-cols-[1.15fr_0.85fr] lg:py-24">
          <div className="space-y-8">
            <span className="inline-flex items-center gap-2 rounded-full border border-sky-200 bg-white/80 px-4 py-2 text-sm font-medium text-sky-700 shadow-sm">
              <Bot className="h-4 w-4" />
              Productive workspaces with room to grow
            </span>
            <div className="space-y-5">
              <h1 className="max-w-3xl text-5xl font-semibold tracking-tight text-slate-950 md:text-6xl">
                Ship faster from a clean, persistent workspace in the browser.
              </h1>
              <p className="max-w-2xl text-lg leading-8 text-slate-600">
                DevNest gives teams a calm place to launch projects, manage workspace lifecycles, and prepare for deeper integrations without the usual setup friction.
              </p>
            </div>
            <div className="flex flex-col gap-3 sm:flex-row">
              <Link href="/signup">
                <a className={cn(buttonVariants({ size: "lg" }), "gap-2 rounded-2xl px-6")}>
                  <span>Start for free</span>
                  <ArrowRight className="h-4 w-4" />
                </a>
              </Link>
              <Link href="/login">
                <a
                  className={cn(
                    buttonVariants({ variant: "secondary", size: "lg" }),
                    "rounded-2xl border border-slate-200 bg-white/80 px-6",
                  )}
                >
                  Login
                </a>
              </Link>
            </div>
          </div>

          <div className="grid gap-5">
            <Card className="overflow-hidden border-white/70 bg-white/75 shadow-[0_30px_70px_-40px_rgba(15,23,42,0.55)] backdrop-blur">
              <CardContent className="space-y-5 p-6">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-slate-500">Workspace Overview</p>
                    <h2 className="mt-1 text-2xl font-semibold text-slate-950">My Team Spaces</h2>
                  </div>
                  <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700">
                    3 active
                  </span>
                </div>
                <div className="grid gap-3">
                  {["Design system refresh", "Telemetry sandbox", "Docs playground"].map((item, index) => (
                    <div
                      key={item}
                      className="flex items-center justify-between rounded-2xl border border-slate-200/80 bg-white px-4 py-3"
                    >
                      <div>
                        <p className="font-medium text-slate-900">{item}</p>
                        <p className="text-sm text-slate-500">Workspace {index + 1}</p>
                      </div>
                      <span className="rounded-full bg-sky-50 px-3 py-1 text-xs font-medium text-sky-700">
                        Running
                      </span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            <FeatureHighlights />
          </div>
        </section>
      </div>
    </main>
  );
}
