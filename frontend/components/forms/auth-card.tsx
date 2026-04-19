"use client";

import Link from "next/link";
import { zodResolver } from "@hookform/resolvers/zod";
import { useRouter } from "next/router";
import { Github, Mail } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { GuestOnly } from "@/components/auth/guest-only";
import { ApiError } from "@/lib/api/error";
import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const loginSchema = z.object({
  username: z.string().min(1, "Enter your username."),
  password: z.string().min(1, "Enter your password."),
});

const signupSchema = z.object({
  username: z.string().min(3, "Use at least 3 characters for your username."),
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(8, "Use at least 8 characters."),
});

type LoginValues = z.infer<typeof loginSchema>;
type SignupValues = z.infer<typeof signupSchema>;
type AuthValues = LoginValues & Partial<SignupValues>;

type AuthCardProps = {
  mode: "login" | "signup";
  title: string;
  description: string;
  submitLabel: string;
  alternateHref: string;
  alternateLabel: string;
};

export function AuthCard({
  mode,
  title,
  description,
  submitLabel,
  alternateHref,
  alternateLabel,
}: AuthCardProps) {
  const router = useRouter();
  const { login, signup, isAuthenticated } = useAuth();
  const [submitError, setSubmitError] = useState<string | null>(null);
  const form = useForm<AuthValues>({
    resolver: zodResolver(mode === "signup" ? signupSchema : loginSchema),
    defaultValues: {
      username: "",
      email: "",
      password: "",
    },
  });

  useEffect(() => {
    if (isAuthenticated) {
      router.replace("/dashboard");
    }
  }, [isAuthenticated, router]);

  const isSubmitting = form.formState.isSubmitting;

  const showRegisteredMessage =
    router.isReady && mode === "login" && router.query.registered === "1";
  const oauthError =
    router.isReady && typeof router.query.oauth_error === "string"
      ? decodeURIComponent(router.query.oauth_error)
      : null;
  const oauthStartHref = (provider: "github" | "google") =>
    `/api/auth/oauth/${provider}/start?source=${encodeURIComponent(mode)}`;

  const handleSubmit = form.handleSubmit(async (values) => {
    setSubmitError(null);

    try {
      if (mode === "signup") {
        await signup({
          username: values.username,
          email: values.email || "",
          password: values.password,
        });
        await router.replace("/login?registered=1");
        return;
      }

      await login({
        username: values.username,
        password: values.password,
      });
      await router.replace("/dashboard");
    } catch (error) {
      setSubmitError(error instanceof ApiError ? error.detail : "Unable to complete authentication.");
    }
  });

  return (
    <GuestOnly>
      <main className="flex min-h-screen items-center justify-center bg-[radial-gradient(circle_at_top,_rgba(14,165,233,0.15),_transparent_32%),linear-gradient(180deg,_#f8fbff_0%,_#f4f7fb_100%)] px-6 py-10">
        <Card className="w-full max-w-md border-white/80 bg-white/90 shadow-[0_24px_60px_-32px_rgba(15,23,42,0.45)] backdrop-blur">
          <CardHeader className="space-y-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-950 text-sm font-semibold text-white">
              DN
            </div>
            <div className="space-y-1">
              <CardTitle className="text-2xl">{title}</CardTitle>
              <CardDescription className="text-sm leading-6 text-slate-600">{description}</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="grid gap-3 sm:grid-cols-2">
              <Button asChild variant="secondary" className="rounded-2xl border border-slate-200 bg-white" type="button">
                <a href={oauthStartHref("github")}>
                  <Github className="h-4 w-4" />
                  GitHub
                </a>
              </Button>
              <Button asChild variant="secondary" className="rounded-2xl border border-slate-200 bg-white" type="button">
                <a href={oauthStartHref("google")}>
                  <Mail className="h-4 w-4" />
                  Google
                </a>
              </Button>
            </div>

            <div className="relative">
              <div className="absolute inset-0 flex items-center">
                <span className="w-full border-t border-slate-200" />
              </div>
              <div className="relative flex justify-center text-xs uppercase tracking-[0.3em] text-slate-400">
                <span className="bg-white px-3">or continue with credentials</span>
              </div>
            </div>

            <form className="space-y-4" onSubmit={handleSubmit}>
              {showRegisteredMessage ? (
                <p className="rounded-2xl bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                  Account created successfully. Please log in.
                </p>
              ) : null}
              {oauthError ? (
                <p className="rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-700">{oauthError}</p>
              ) : null}

              <div className="space-y-2">
                <Label htmlFor="username">Username</Label>
                <Input id="username" placeholder="timchoi" {...form.register("username")} />
                {form.formState.errors.username ? (
                  <p className="text-sm text-rose-600">{form.formState.errors.username.message}</p>
                ) : null}
              </div>

              {mode === "signup" ? (
                <div className="space-y-2">
                  <Label htmlFor="email">Email</Label>
                  <Input id="email" type="email" placeholder="you@company.com" {...form.register("email")} />
                  {form.formState.errors.email ? (
                    <p className="text-sm text-rose-600">{form.formState.errors.email.message}</p>
                  ) : null}
                </div>
              ) : null}

              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <Input id="password" type="password" placeholder="Enter your password" {...form.register("password")} />
                {form.formState.errors.password ? (
                  <p className="text-sm text-rose-600">{form.formState.errors.password.message}</p>
                ) : null}
              </div>

              {submitError ? <p className="rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-700">{submitError}</p> : null}

              <Button type="submit" className="h-11 w-full rounded-2xl" disabled={isSubmitting}>
                {isSubmitting ? "Please wait..." : submitLabel}
              </Button>
            </form>

            <p className="text-center text-sm text-slate-500">
              <Link href={alternateHref}>
                <a className="font-medium text-slate-950 underline-offset-4 hover:underline">{alternateLabel}</a>
              </Link>
            </p>
          </CardContent>
        </Card>
      </main>
    </GuestOnly>
  );
}
