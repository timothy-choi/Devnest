"use client";

import { useRouter } from "next/router";
import type { ReactNode } from "react";
import { useEffect } from "react";

import { useAuth } from "@/hooks/use-auth";

export function GuestOnly({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { isAuthenticated, isLoading, isCheckingSession } = useAuth();

  useEffect(() => {
    if (!isLoading && !isCheckingSession && isAuthenticated) {
      router.replace("/dashboard");
    }
  }, [isAuthenticated, isLoading, isCheckingSession, router]);

  if (isLoading || isCheckingSession) {
    return null;
  }

  return <>{children}</>;
}
