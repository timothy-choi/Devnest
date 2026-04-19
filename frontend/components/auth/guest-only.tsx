"use client";

import { useRouter } from "next/router";
import type { ReactNode } from "react";
import { useEffect } from "react";

import { useAuth } from "@/hooks/use-auth";

export function GuestOnly({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { isAuthenticated, isLoading, isCheckingSession } = useAuth();

  useEffect(() => {
    if (typeof window !== "undefined") {
      const workspaceReturnTarget = window.sessionStorage.getItem("devnestWorkspaceReturnTarget");
      const navEntry = window.performance.getEntriesByType("navigation")[0];
      const navType =
        navEntry && "type" in navEntry
          ? String((navEntry as PerformanceNavigationTiming).type || "")
          : "";

      if (workspaceReturnTarget === "/dashboard" && navType === "back_forward") {
        window.sessionStorage.removeItem("devnestWorkspaceReturnTarget");
        window.location.replace("/dashboard");
        return;
      }
    }

    if (!isLoading && !isCheckingSession && isAuthenticated) {
      router.replace("/dashboard");
    }
  }, [isAuthenticated, isLoading, isCheckingSession, router]);

  if (isLoading || isCheckingSession) {
    return null;
  }

  return <>{children}</>;
}
