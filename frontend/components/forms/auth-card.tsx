"use client";

import Link from "next/link";
import { zodResolver } from "@hookform/resolvers/zod";
import { Github, Mail } from "lucide-react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const authSchema = z.object({
  name: z.string().optional(),
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(8, "Use at least 8 characters."),
});

type AuthValues = z.infer<typeof authSchema>;

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
  const form = useForm<AuthValues>({
    resolver: zodResolver(authSchema),
    defaultValues: {
      name: "",
      email: "",
      password: "",
    },
  });

  return (
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
            <Button variant="secondary" className="rounded-2xl border border-slate-200 bg-white">
              <Github className="h-4 w-4" />
              GitHub
            </Button>
            <Button variant="secondary" className="rounded-2xl border border-slate-200 bg-white">
              <Mail className="h-4 w-4" />
              Google
            </Button>
          </div>

          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <span className="w-full border-t border-slate-200" />
            </div>
            <div className="relative flex justify-center text-xs uppercase tracking-[0.3em] text-slate-400">
              <span className="bg-white px-3">or continue with email</span>
            </div>
          </div>

          <form
            className="space-y-4"
            onSubmit={form.handleSubmit(() => {
              form.reset(form.getValues());
            })}
          >
            {mode === "signup" ? (
              <div className="space-y-2">
                <Label htmlFor="name">Full name</Label>
                <Input id="name" placeholder="Ada Lovelace" {...form.register("name")} />
              </div>
            ) : null}

            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input id="email" type="email" placeholder="you@company.com" {...form.register("email")} />
              {form.formState.errors.email ? (
                <p className="text-sm text-rose-600">{form.formState.errors.email.message}</p>
              ) : null}
            </div>

            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input id="password" type="password" placeholder="Enter your password" {...form.register("password")} />
              {form.formState.errors.password ? (
                <p className="text-sm text-rose-600">{form.formState.errors.password.message}</p>
              ) : null}
            </div>

            <Button type="submit" className="h-11 w-full rounded-2xl">
              {submitLabel}
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
  );
}
