"use client";

import { useRouter } from "next/router";
import type { ReactNode } from "react";
import { useEffect } from "react";

import { useAuth } from "@/hooks/use-auth";

export function AuthGuard({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { isAuthenticated, isLoading, isCheckingSession } = useAuth();

  useEffect(() => {
    if (!isLoading && !isCheckingSession && !isAuthenticated) {
      router.replace("/login");
    }
  }, [isAuthenticated, isLoading, isCheckingSession, router]);

  if (isLoading || (isCheckingSession && !isAuthenticated) || !isAuthenticated) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-[linear-gradient(180deg,_#f8fafc_0%,_#eef2ff_100%)] px-6">
        <div className="rounded-3xl border border-white/70 bg-white/85 px-8 py-6 text-center shadow-[0_20px_55px_-40px_rgba(15,23,42,0.45)]">
          <p className="text-lg font-semibold text-slate-950">Checking your session...</p>
          <p className="mt-2 text-sm text-slate-600">We&apos;re confirming your DevNest access.</p>
        </div>
      </main>
    );
  }

  return <>{children}</>;
}
