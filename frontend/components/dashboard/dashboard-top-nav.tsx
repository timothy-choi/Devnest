import Link from "next/link";
import { Activity, Bell, ChevronDown } from "lucide-react";
import { useRouter } from "next/router";

import { useAuth } from "@/hooks/use-auth";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";

export function DashboardTopNav({
  unreadCount = 0,
  onOpenNotifications,
}: {
  unreadCount?: number;
  onOpenNotifications: () => void;
}) {
  const router = useRouter();
  const { user, logout } = useAuth();
  const initials = user?.displayName
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "DN";

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
          <Button
            variant="secondary"
            size="icon"
            className="relative rounded-full border border-slate-200 bg-white"
            onClick={onOpenNotifications}
          >
            <Bell className="h-4 w-4" />
            {unreadCount ? (
              <span className="absolute -right-0.5 -top-0.5 inline-flex min-w-5 items-center justify-center rounded-full bg-sky-500 px-1.5 text-[10px] font-semibold text-white">
                {unreadCount > 9 ? "9+" : unreadCount}
              </span>
            ) : null}
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className="flex items-center gap-3 rounded-full border border-slate-200 bg-white px-2.5 py-2 shadow-sm transition hover:border-slate-300"
              >
                <Avatar className="h-9 w-9">
                  <AvatarFallback>{initials}</AvatarFallback>
                </Avatar>
                <div className="hidden text-left sm:block">
                  <p className="text-sm font-medium text-slate-900">{user?.displayName || user?.username || "DevNest user"}</p>
                  <p className="text-xs text-slate-500">{user?.email || "Signed in"}</p>
                </div>
                <ChevronDown className="hidden h-4 w-4 text-slate-500 sm:block" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuItem
                className="cursor-pointer"
                onClick={() => {
                  void router.push("/system-status");
                }}
              >
                <Activity className="mr-2 h-4 w-4" />
                System status
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={async () => {
                  await logout();
                  await router.push("/login");
                }}
              >
                Sign out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </header>
  );
}
