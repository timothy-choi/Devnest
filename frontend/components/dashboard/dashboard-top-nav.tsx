import Link from "next/link";
import { Bell, ChevronDown } from "lucide-react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";

export function DashboardTopNav() {
  return (
    <header className="sticky top-0 z-20 border-b border-white/70 bg-white/80 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4 sm:px-6 lg:px-8">
        <div className="flex items-center gap-3">
          <Link href="/">
            <a className="flex items-center gap-3">
              <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-slate-950 text-sm font-semibold text-white">
                DN
              </div>
              <div>
                <p className="text-sm font-semibold text-slate-950">DevNest</p>
                <p className="text-xs text-slate-500">Workspace hub</p>
              </div>
            </a>
          </Link>
        </div>

        <div className="flex items-center gap-3">
          <Button variant="secondary" size="icon" className="rounded-full border border-slate-200 bg-white">
            <Bell className="h-4 w-4" />
          </Button>
          <button
            type="button"
            className="flex items-center gap-3 rounded-full border border-slate-200 bg-white px-2.5 py-2 shadow-sm transition hover:border-slate-300"
          >
            <Avatar className="h-9 w-9">
              <AvatarFallback>TC</AvatarFallback>
            </Avatar>
            <div className="hidden text-left sm:block">
              <p className="text-sm font-medium text-slate-900">Tim Choi</p>
              <p className="text-xs text-slate-500">Frontend preview</p>
            </div>
            <ChevronDown className="hidden h-4 w-4 text-slate-500 sm:block" />
          </button>
        </div>
      </div>
    </header>
  );
}
