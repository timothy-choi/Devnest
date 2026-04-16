"use client";

import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  browserApi,
  isUnauthorizedError,
  type AuthUser,
  type LoginInput,
  type SignupInput,
  type SignupSuccess,
} from "@/lib/api/browser-client";
import type { CurrentUser } from "@/types/auth";

type AuthContextValue = {
  user: CurrentUser | null;
  isLoading: boolean;
  isCheckingSession: boolean;
  isAuthenticated: boolean;
  login: (values: LoginInput) => Promise<CurrentUser>;
  signup: (values: SignupInput) => Promise<SignupSuccess>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);
const AUTH_QUERY_KEY = ["auth", "me"];

function normalizeUser(user: AuthUser): CurrentUser {
  return user;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();

  const authQuery = useQuery({
    queryKey: AUTH_QUERY_KEY,
    queryFn: async () => {
      try {
        const response = await browserApi.auth.me();
        return response.user ? normalizeUser(response.user) : null;
      } catch (error) {
        if (isUnauthorizedError(error)) {
          return null;
        }
        throw error;
      }
    },
    retry: false,
  });

  const loginMutation = useMutation({
    mutationFn: async (values: LoginInput) => {
      const response = await browserApi.auth.login(values);
      return normalizeUser(response.user);
    },
    onSuccess: (user) => {
      queryClient.setQueryData(AUTH_QUERY_KEY, user);
    },
  });

  const signupMutation = useMutation({
    mutationFn: (values: SignupInput) => browserApi.auth.signup(values),
    onSuccess: () => {
      queryClient.setQueryData(AUTH_QUERY_KEY, null);
      void queryClient.invalidateQueries({ queryKey: AUTH_QUERY_KEY });
    },
  });

  const logoutMutation = useMutation({
    mutationFn: async () => {
      await browserApi.auth.logout();
    },
    onSuccess: () => {
      queryClient.setQueryData(AUTH_QUERY_KEY, null);
      queryClient.removeQueries({ queryKey: ["workspaces"] });
    },
  });

  return (
    <AuthContext.Provider
      value={{
        user: authQuery.data ?? null,
        isLoading: authQuery.isLoading,
        isCheckingSession: authQuery.fetchStatus === "fetching",
        isAuthenticated: Boolean(authQuery.data),
        login: async (values) => loginMutation.mutateAsync(values),
        signup: (values) => signupMutation.mutateAsync(values),
        logout: async () => logoutMutation.mutateAsync(),
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }

  return context;
}
